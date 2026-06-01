"""
wiki_retrieval.py — Hybrid semantic + keyword retrieval for GSM Outdoors wiki.

Scoring model:
  semantic (vector cosine × 10)  — finds pages by meaning, not exact words
  keyword  (topic match × 1-5)   — boosts exact model numbers and brand names
  combined — semantic wins for natural language, keyword wins for model numbers

If vector_store is not built yet, falls back to keyword-only automatically.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR         = Path(__file__).resolve().parent
WIKI_DIR         = BASE_DIR / "wiki"
INDEX_PATH       = WIKI_DIR / "index.md"
SUPPLEMENTS_PATH = WIKI_DIR / ".page_keyword_supplements.json"

SKIP_PAGES    = {"index.md", "log.md", "lint-report.md"}
OVERVIEW_PAGES = {
    # "fishing/fishing-overview.md",   # 2026-05-21: page not yet created
    # "hunting/hunting-overview.md",   # 2026-05-21: page not yet created
    # "wireless/wireless-overview.md", # 2026-05-21: page not yet created
    # Brand hub pages — demoted when any sub-page scores > 0
    # so specific series pages always rank above the hub
    "fishing/phenix-rods.md",
    "fishing/dobyns-rods.md",
    "fishing/bucca-brand.md",
}

# ── Vector store — imported lazily ───────────────────────────────────────────
try:
    from vector_store import (
        hybrid_score_pages,
        semantic_search,
        get_top_sections,
        get_top_sections_with_variants,  # variant-aware hybrid retrieval
        build_context_from_sections,
        rerank_sections,                 # cross-encoder reranking (v3)
        is_ready as vector_store_ready,
    )
    _VECTOR_AVAILABLE = True
except ImportError:
    _VECTOR_AVAILABLE = False
    def vector_store_ready() -> bool: return False
    def get_top_sections(*a, **kw): return []
    def get_top_sections_with_variants(*a, **kw): return []
    def build_context_from_sections(*a, **kw): return "", []
    def rerank_sections(query, sections): return sections


# ─────────────────────────────────────────────────────────────────────────────
# Keyword registry — reads from ingest_customer.py WIKI_PAGES at runtime
# Falls back to PAGE_KEYWORDS_BASE if ingest_customer.py unavailable
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Query expansion table — intent-based, brand-agnostic
# ─────────────────────────────────────────────────────────────────────────────
# Each entry has:
#   "any"  — at least one phrase from this list must appear in the query
#   "all"  — ALL phrases from this list must appear (optional second gate)
#   "append" — text appended to the query before embedding
#
# Rules are intent-based so they work across ALL brands automatically.
# To add a new intent pattern: append a new dict to this list.
# ─────────────────────────────────────────────────────────────────────────────
_QUERY_EXPANSIONS: List[Dict] = [
    # Early breakage → manufacturing defect claim (any brand)
    {
        "any":    ["broke", "broken", "snapped", "cracked"],
        "all":    ["cast", "first use", "immediately", "right away",
                   "new rod", "brand new", "third", "second", "first time"],
        "append": "manufacturing defect claim warranty photo inspection do not ship",
    },
    # Small part request → care/maintenance page (any brand)
    {
        "any":    ["replacement guide", "replacement tip", "free guide", "free tip",
                   "get a guide", "get a tip", "broken guide", "missing guide",
                   "guide broke", "guide fell", "can i get a free"],
        "append": "free parts request rod care maintenance guides tips",
    },
    # Second/non-original owner → no-hassle replacement (any brand)
    {
        "any":    ["second owner", "used rod", "gave me", "buddy gave", "friend gave",
                   "inherited", "bought used", "gift rod", "not original owner",
                   "not the original", "previous owner", "hand me down"],
        "append": "no-hassle replacement available anyone tier fee second owner",
    },
    # Warranty transfer question (any brand)
    {
        "any":    ["transfer warranty", "warranty transfer", "does warranty transfer",
                   "passes warranty", "warranty pass"],
        "append": "warranty transfer original owner no-hassle replacement available",
    },
    # Feeder not working → troubleshooting page (hunting brands)
    {
        "any":    ["feeder not", "feeder stopped", "feeder won't", "feeder doesn't",
                   "motor not spinning", "not dispensing", "spinner not working",
                   "feeder error", "feeder jam"],
        "append": "troubleshooting motor timer battery feeder repair error",
    },
    # Camera / cellular not working → troubleshooting (wireless brands)
    {
        "any":    ["not sending", "no signal", "no photos", "camera not working",
                   "won't connect", "won't sync", "not connecting", "no pictures",
                   "camera offline", "not transmitting"],
        "append": "troubleshooting cellular signal SIM card connection app",
    },
    # Recon Elite or Maxim bass rod queries → freshwater bass specialty page
    {
        "any":    ["recon elite", "maxim bass", "recon elite vs maxim",
                   "maxim vs recon", "what is the maxim rod"],
        "append": "phenix freshwater bass rod recon elite maxim value series comparison",
    },
    # Walleye rod query → freshwater specialty page
    {
        "any":    ["walleye rod", "walleye fishing", "m1 walleye", "rod for walleye",
                   "walleye series", "phenix walleye"],
        "append": "phenix M1 walleye rod freshwater specialty series",
    },
    # Replacement / warranty cost queries → tier fees page
    # Catches "cheapest replacement", "how much does it cost", "what's the minimum fee" etc.
    {
        "any":    ["cheapest", "how much", "minimum cost", "what's the cost",
                   "what does it cost", "replacement cost", "how much does it cost",
                   "how much will it cost", "how much is it", "how much to replace",
                   "what will it cost", "price to replace", "cost to replace",
                   "what's the fee", "replacement fee", "tier fee", "what tier",
                   "which tier", "minimum fee", "minimum replacement", "get out of"],
        "append": "tier fee replacement cost tier 1 tier 2 tier 3 warranty fee phenix tier",
    },
    # Model-specific cost queries → tier fees clarification sections
    # "abyss hd" cost queries bleed into saltwater-pelagic spec page without this
    {
        "any":    ["abyss hd", "abyss hd cost", "abyss hd tier", "abyss hd replace"],
        "append": "abyss hd tier 3 replacement fee tier fee clarification",
    },
    {
        "any":    ["axis ml", "axis xh", "axis x2h", "axis x4h", "axis tier",
                   "axis replacement", "axis fee", "axis action"],
        "append": "axis tier fee action replacement tier 3 tier 4 clarification",
    },
    # Iron Feather disambiguation → trout/ultralight page (Tier 5, premium series)
    # Without this, "iron feather" embeddings bleed into generic "feather" results
    {
        "any":    ["iron feather"],
        "append": "phenix iron feather trout ultralight series tier 5 premium",
    },
    # Feather (without "iron") → freshwater bass page (Tier 1, bass rod)
    # "feather" alone is ambiguous with "iron feather" in vector space — pin it to bass
    {
        "any":    ["feather rod", "feather series", "the feather", "my feather",
                   "feather bass", "feather ftx", "feather tier"],
        "append": "phenix feather series freshwater bass rod tier 1",
    },
    # Upgrade queries → warranty page (upgrade is part of No-Hassle Replacement program)
    # Catches "upgrade to Ultra MBX from my Feather" — bot was saying no upgrade exists
    {
        "any":    ["upgrade", "trade up", "step up", "want to upgrade", "can i upgrade",
                   "upgrade my rod", "upgrade to", "upgrading my"],
        "append": "warranty upgrade fee replacement broken rod logo section no-hassle submit",
    },
    # Blank / section replacement fee queries → tier-fees blanks table
    # Uses all+any compound: "blank" must be present AND one of tier/fee/list/cost/replace
    # Catches: "blank fee", "blank tier", "list all blanks in tier 1", "blanks in tier 2",
    #          "what blanks are in tier 3", "cost to replace a blank", etc.
    # Pure "blank" alone (e.g. "blank check") won't fire — needs tier/fee context.
    {
        "all":    ["blank"],
        "any":    ["tier", "fee", "cost", "price", "list", "replace", "warranty",
                   "how much", "what is", "same", "different", "section"],
        "append": "blank section replacement fee blanks sections tier fee lower table phenix",
    },
    # Rod return / what to cut → no-hassle How to Submit section
    # Catches "What do I cut off when I send in a broken Phenix?" style queries
    {
        "any":    ["cut off", "what to cut", "what do i cut", "do i cut",
                   "cut when", "cut from", "cut the rod", "cut my rod",
                   "send in", "ship in", "mail in", "sending in", "mailing in",
                   "shipping in", "sending my rod", "mailing my rod", "shipping my rod"],
        "append": "logo section cut warranty return process no-hassle replacement submit",
    },
    # Generic broken rod without first-use qualifier → warranty/no-hassle page
    # (The early-breakage rule above requires first-use context; this catches everything else)
    {
        "any":    ["broken rod", "rod broke", "broke my rod", "rod snapped",
                   "snapped my rod", "my rod broke", "rod is broken", "rod broke",
                   "rod fell apart", "literally fell apart", "rod cracked",
                   "rod split", "rod is snapped"],
        "append": "no-hassle replacement warranty tier fee logo section",
    },
]


def _expand_query(query: str) -> str:
    """Append retrieval-boosting terms based on detected query intent."""
    q = query.lower()
    appended: List[str] = []
    for rule in _QUERY_EXPANSIONS:
        any_match = any(p in q for p in rule["any"])
        if not any_match:
            continue
        if "all" in rule:
            if not any(p in q for p in rule["all"]):
                continue
        appended.append(rule["append"])
    return query + (" " + " ".join(appended) if appended else "")


# Fallback base map (used only if ingest_customer.py cannot be imported)
PAGE_KEYWORDS_BASE: Dict[str, List[str]] = {
    "fishing/phenix-rods.md":              ["phenix", "elixir", "mirage", "iron feather", "phx", "no-hassle",
                                            "abyss", "trifecta", "cicada", "black diamond", "redeye"],
    "fishing/phenix-rods-freshwater-bass.md": ["m1 bass", "feather", "ultra mbx", "k2", "ultra swimbait",
                                            "classic bfs", "umbx", "feather ftx", "phenix bass", "bass rod",
                                            "tournament bass", "swimbait rod", "swimbait fishing",
                                            "big swimbait", "bass swimbait"],
    "fishing/phenix-rods-freshwater-specialty.md": ["kokanee", "kokanee reaper", "maxim", "recon elite",
                                            "crankbait xg", "crankbait composite", "black chrome",
                                            "super flipper", "m1 walleye", "walleye rod",
                                            "crankbait rod", "crankbait",
                                            # Q13 XG vs Composite X
                                            "difference crankbait", "xg vs composite", "glass rod",
                                            "s-glass", "glass or graphite", "crankbait xg composite",
                                            # Q15 Black Chrome designed for
                                            "black chrome designed", "black chrome steelhead",
                                            "black chrome for", "black chrome drift",
                                            # Q17 Recon Elite vs Maxim comparison
                                            "recon elite compare", "recon elite vs maxim",
                                            "compare maxim recon", "recon elite maxim",
                                            # Q18 Super Flipper
                                            "super flipper built", "flipper rod", "flipper flipping",
                                            "flipping rod", "heavy cover flipping",
                                            # Q29 XG glass
                                            "xg glass", "crankbait glass", "phenix glass rod"],
    "fishing/phenix-rods-salmon-steelhead.md": ["cicada", "trifecta", "trifecta pro", "trifecta lite",
                                            "salmon rod", "steelhead rod", "phenix salmon",
                                            "salmon", "steelhead",
                                            # Q23 Trifecta Lite casting
                                            "trifecta lite casting", "trifecta lite spinning",
                                            "trifecta casting model", "trifecta lite only",
                                            # Q22 2-piece salmon travel
                                            "2-piece salmon", "two piece salmon", "travel salmon"],
    "fishing/phenix-rods-saltwater-inshore.md": ["m1 inshore", "rts inshore", "titan slow", "titan popping",
                                            "megalodon", "phenix inshore", "inshore rod",
                                            "tuna", "tuna rod", "tuna jigging", "tuna recommendation",
                                            "slow pitch jigging", "slow pitch", "jigging rod"],
    "fishing/phenix-rods-saltwater-pelagic.md": ["abyss", "abyss hd", "axis", "black diamond", "pandora",
                                            "black diamond hybrid", "phenix saltwater", "offshore rod",
                                            "live bait", "kelp", "kelp bed", "female angler",
                                            "designed for women", "pandora rod"],
    "fishing/phenix-rods-tier-fees.md":    ["tier fee", "replacement fee", "no-hassle fee", "phenix tier",
                                            "warranty cost", "replacement cost", "return shipping",
                                            "tier 1", "tier 2", "tier 3", "tier 4", "tier 5",
                                            "feather", "iron feather", "cost to replace",
                                            "how much", "upgrade fee", "how much does it cost",
                                            "replace a phenix", "tier fee",
                                            # Q9 XG cost
                                            "crankbait xg", "crankbait xg cost", "replace xg",
                                            "xg replacement", "crankbait xg tier",
                                            # Q10 Axis action fee difference
                                            "axis ml", "axis xh", "axis x4h", "axis x2h",
                                            "axis action", "axis fee", "axis replacement",
                                            "axis tier", "difference axis",
                                            # Q7 Abyss HD
                                            "abyss hd", "abyss hd tier", "abyss hd cost",
                                            # Q11 blank fee
                                            "blank fee", "replacement blank", "black diamond blank",
                                            # Q27 minimum cost
                                            "minimum cost", "cheapest replacement", "minimum replacement",
                                            "absolute minimum", "cheapest phenix replacement",
                                            # Q28 Black Chrome 9'2" shipping
                                            "black chrome 9", "shipping tier", "9 foot rod shipping"],
    "fishing/phenix-rods-travel.md":       ["redeye", "redeye travel", "travel rod", "phenix travel",
                                            "redeye saltwater", "redeye freshwater",
                                            # Q24 RedEye bass tropical
                                            "redeye bass", "bass travel rod", "tropical bass",
                                            "tropical fishing rod", "travel bass", "bass trip",
                                            "tropical inshore", "redeye freshwater bass"],
    "fishing/phenix-rods-trout-ultralight.md": ["iron feather", "elixir", "mirage", "dragonfly",
                                            "trout rod", "ultralight rod", "phenix trout",
                                            "panfish", "crappie rod", "crappie",
                                            # Q25 Mirage vs Dragonfly
                                            "mirage vs dragonfly", "difference mirage dragonfly",
                                            "mirage dragonfly compare", "mirage and dragonfly",
                                            "difference between mirage", "mirage or dragonfly"],
    "fishing/phenix-rods-warranty.md":     ["phenix warranty", "no-hassle", "boron legacy", "defect claim",
                                            "warranty program", "logo section", "mailing address",
                                            "5250 frye", "warranty replacement", "phenix broken rod",
                                            "warranty", "ship rod", "dealer", "snapped", "rod broke",
                                            "broken", "process warranty", "upgrade warranty", "rod snapped",
                                            "warranty through dealer", "warranty steps",
                                            # Q5 payment options
                                            "pay by phone", "how to pay", "payment", "payment method",
                                            "pay for warranty", "check or money order", "credit card",
                                            "pay for replacement", "how do i pay"],
    "fishing/phenix-rods-pricing.md":      ["phenix price", "phenix msrp", "phenix cost", "how much phenix",
                                            "cicada", "affordable", "premium", "most affordable",
                                            "most premium", "how much", "cicada cost", "cheapest"],
    "fishing/dobyns-rods.md":              ["dobyns", "dobyns rod", "champion", "sierra", "xtasy",
                                            "fury", "colt", "kaden", "gary dobyns", "maverick",
                                            "dobyns brand", "dobyns overview", "dobyns series overview",
                                            "dobyns crappie rod", "dobyns livescope",
                                            "dobyns budget rod", "dobyns beginner rod",
                                            "dobyns travel rod",
                                            "crappie rod", "crappie"],
    "fishing/dobyns-rods-product-catalog.md": ["dobyns", "xtasy", "drx", "champion extreme", "champion xp",
                                            "kaden", "kaden travel", "sierra", "sierra micro",
                                            "sierra ultra finesse", "sierra trout", "fury", "colt",
                                            "josh jones", "eric cagle", "sam sobi", "bullshad rod",
                                            "d-blade", "d-swim", "d-nail", "dobyns model", "dobyns series",
                                            "dobyns fuji torzite", "toray nano graphite", "dobyns models",
                                            "dobyns rod specs", "dobyns rod models", "fuji alconite",
                                            "dobyns swimbait rod", "dobyns crankbait rod",
                                            "e.c. special", "ec special", "eric cagle crappie",
                                            "josh jones hyperlite", "mike bucca bullshad",
                                            "sam sobi signature"],
    "fishing/dobyns-rods-warranty.md":     ["dobyns warranty", "dobyns limited lifetime", "dobyns defect",
                                            "maverick reel warranty", "dobyns original owner",
                                            "dobyns authorized dealer", "dobyns 60 day",
                                            "dobyns manufacturing defect", "dobyns rod broke",
                                            "dobyns rod warranty", "dobyns warranty coverage",
                                            "dobyns limited lifetime warranty", "dobyns 1 year warranty",
                                            "dobyns colt warranty", "dobyns maverick warranty",
                                            "dobyns warranty cover", "dobyns accidental breakage",
                                            "dobyns 60 days", "dobyns 60 day defect",
                                            "dobyns warranty transfer", "dobyns second owner",
                                            "dobyns discontinued model", "dobyns defect photos",
                                            "dobyns rod snapped warranty", "dobyns stick snapped",
                                            "dobyns defective", "dobyns warranty process",
                                            "dobyns defect claim", "dobyns rod defect",
                                            "dobyns first time broke", "dobyns warranty eligibility",
                                            "original owner", "warranty transfer", "second owner",
                                            "60 days",
                                            # Natural-language phrase matches
                                            "my dobyns rod", "limited lifetime", "manufacturing defect",
                                            "accidental breakage", "discontinued", "under warranty",
                                            "defect claim", "used dobyns", "still under warranty",
                                            "dobyns rod defective",
                                            # Maverick reel warranty (Q28)
                                            "maverick reel", "dobyns maverick reel", "dobyns maverick",
                                            # Rod snapped defect queries (Q67)
                                            "dobyns rod snapped", "defective", "snapped first time",
                                            "is it defective",
                                            # Model-specific early-break queries
                                            "fury broke", "colt broke", "xtasy broke",
                                            "champion broke", "sierra broke", "kaden broke",
                                            "my fury broke", "my colt broke", "my xtasy broke",
                                            "broke on the", "broke on first", "broke on second",
                                            "broke on third", "third cast", "first cast", "second cast",
                                            "broke immediately", "broke right away", "brand new broke",
                                            "new rod broke", "broke after one", "broke after two",
                                            "broke after three", "first use", "first time using"],
    "fishing/dobyns-rods-replacement.md":  ["dobyns replacement", "dobyns no hassle", "dobyns broken rod",
                                            "dobyns logo section", "dobyns warranty form",
                                            "dobyns upgrade", "dobyns dealer replacement",
                                            "5250 frye", "dobyns ship",
                                            "dobyns rod replacement", "dobyns replacement process",
                                            "dobyns broken how", "dobyns whole rod",
                                            "dobyns padded envelope", "dobyns pay by phone",
                                            "dobyns 2 weeks", "dobyns replacement time",
                                            "dobyns dealer warranty", "dobyns upgrade series",
                                            "dobyns upgrade broken", "dobyns warranty address",
                                            "dobyns check money order", "dobyns swap broken rod",
                                            "dobyns can i upgrade", "dobyns logo cut",
                                            "dobyns how to replace", "dobyns replacement steps",
                                            "logo section", "padded envelope",
                                            "whole rod", "check money order", "2 weeks processing",
                                            "cut rod", "where to cut", "how to cut", "irving tx",
                                            "dobyns rod broke", "replacement steps", "how to get replacement",
                                            # Natural-language phrase matches
                                            "broken dobyns", "whole dobyns", "ship my dobyns",
                                            "cut my broken", "upgrade my dobyns", "swap my broken",
                                            "where do i ship", "get a replacement",
                                            "through a dealer", "process through"],
    "fishing/dobyns-rods-tier-fees.md":    ["dobyns tier fee", "dobyns replacement cost",
                                            "dobyns warranty cost", "dobyns shipping fee",
                                            "dobyns colt fee", "dobyns fury fee", "dobyns champion fee",
                                            "dobyns xtasy fee", "dobyns upgrade fee", "how much dobyns",
                                            "dobyns how much", "dobyns cost replace",
                                            "cost replace dobyns", "dobyns colt cost",
                                            "dobyns maverick cost", "dobyns fury cost",
                                            "dobyns sierra cost", "dobyns kaden cost",
                                            "dobyns champion cost", "dobyns xtasy cost",
                                            "dobyns return shipping fee", "dobyns return shipping",
                                            "dobyns sales tax", "dobyns international shipping",
                                            "dobyns alaska hawaii", "dobyns replacement price",
                                            "dobyns warranty price", "dobyns upgrade fee formula",
                                            "dobyns tier", "replace dobyns rod",
                                            "sales tax", "alaska", "hawaii",
                                            # Natural-language phrase matches
                                            "cost to replace", "to replace a dobyns",
                                            "replace a dobyns", "replace my dobyns",
                                            "dobyns replacement", "warranty upgrade fee",
                                            "international shipping", "how much will it cost",
                                            "what will it cost"],
    "fishing/dobyns-rods-care.md":         ["dobyns rod care", "dobyns cleaning", "dobyns guide cleaning",
                                            "dobyns cork", "dobyns storage", "dobyns maintenance",
                                            "dobyns replacement tip", "dobyns free parts", "u-40",
                                            "dobyns saltwater cleaning",
                                            "dobyns clean", "dobyns rod cleaning",
                                            "dobyns clean guides", "dobyns guide care",
                                            "dobyns cork care", "dobyns cork grip",
                                            "dobyns rod storage", "dobyns store rod",
                                            "dobyns free tip", "dobyns free guide",
                                            "dobyns rod maintain", "dobyns clean after saltwater",
                                            "dobyns care instructions",
                                            "cork", "cork care", "guide cleaning", "rod storage",
                                            # Natural-language phrase matches
                                            "clean my dobyns", "store my dobyns",
                                            "free replacement tip", "free replacement guides",
                                            "my dobyns rod guides", "dobyns guide",
                                            # Free parts request — must beat replacement page on these
                                            "replacement guide", "replacement tip", "free guide",
                                            "free tip", "get a guide", "get a tip",
                                            "can i get a free", "request a guide", "request a tip",
                                            "how do i get a replacement guide", "how do i get a replacement tip",
                                            "guide replacement", "tip replacement", "broken guide",
                                            "guide fell off", "missing guide", "guide broke"],
    "fishing/dobyns-lures.md":             ["d-blade", "d-blade advantage", "d-blade beast",
                                            "dobyns spinnerbait", "dobyns jig", "football jig",
                                            "d-swim", "d-nail", "dobyns tackle", "dobyns lures",
                                            "dobyns baits", "gamakatsu",
                                            # Single-word power keywords
                                            "dobyns spinnerbaits", "dobyns d-blade spinnerbait",
                                            "dobyns advantage spinnerbait", "dobyns beast spinnerbait",
                                            "dobyns football jigs", "dobyns jig hook",
                                            "dobyns swimbait heads", "dobyns terminal",
                                            "dobyns make jigs", "dobyns lure types",
                                            "gamakatsu hook", "dobyns d-swim bait",
                                            "dobyns nail weight", "dobyns football head",
                                            "spinnerbait", "spinnerbaits",
                                            "swimbait head", "swimbait heads", "nail weight",
                                            "football head", "d-blade spinnerbait",
                                            "ball bearing swivel"],
    "fishing/dobyns-reels-combos.md":      ["maverick reel", "maverick spinning", "maverick casting",
                                            "mv 2000", "mv 2500", "dobyns combo", "maverick combo",
                                            "dobyns rod reel combo", "dobyns gear ratio",
                                            # Single-word power keywords
                                            "dobyns rod combo", "dobyns spinning reel",
                                            "dobyns casting reel", "dobyns reel gear ratio",
                                            "6.5 gear ratio", "7.2 gear ratio", "8.1 gear ratio",
                                            "mv 2000 reel", "mv 2500 reel", "dobyns reels",
                                            "maverick reel specs", "dobyns combo skus",
                                            "dobyns sells combos", "dobyns reel warranty",
                                            "gear ratio", "spinning reel",
                                            "casting reel", "reel combo", "reels"],
    "fishing/bucca-brand.md":              ["bucca", "bucca brand", "mike bucca", "trick shad",
                                            "baby bull shad", "bull mullet", "swimbait", "glide bait",
                                            "bucca saltwater", "bucca freshwater", "bucca abs plastic",
                                            "bucca swimbaits", "bucca lure brand", "bucca products",
                                            "bucca bull rat", "bucca bull wake", "bucca overview"],
    "fishing/bucca-brand-product-catalog.md": ["bucca", "trick shad", "BUC-TS6", "BUC-BBS375",
                                            "BUC-BBG375", "BUC-BBR35", "BUC-BM55", "BUC-BM8",
                                            "baby bull gill", "baby bull rat", "bull wake",
                                            "buzzing baby bull shad", "weedless baby bull shad",
                                            "lil baby bull shad", "replacement tail", "SKU", "colors",
                                            "trick shad sizes", "trick shad 4 inch", "trick shad 6 inch",
                                            "trick shad 8 inch", "trick shad weight", "trick shad hooks",
                                            "baby bull shad segments", "bull mullet hooks",
                                            "bull mullet saltwater", "bull mullet sizes",
                                            "baby bull gill colors", "baby bull rat topwater",
                                            "buzzing baby bull topwater", "weedless swimbait bucca",
                                            "bucca replacement tails", "bucca product specs",
                                            "bucca sku", "bull mullet 8 inch", "bull mullet 5.5 inch",
                                            "bull mullet", "hooks"],
    "fishing/bucca-brand-warranty.md":     ["bucca warranty", "bucca brand warranty", "RA number",
                                            "return authorization", "bucca defect", "bucca replacement",
                                            "21 days", "proof of purchase", "manufacturing defect",
                                            "broken bucca", "bucca broken", "defective bucca",
                                            "bucca trick shad broken", "trick shad broken",
                                            "bucca damaged", "bucca not working",
                                            # Phrase-level strengthening
                                            "bucca product warranty", "bucca has warranty",
                                            "bucca brand has warranty", "bucca warranty filing",
                                            "bucca file warranty", "bucca receipt needed",
                                            "bucca proof of purchase", "bucca ship before ra",
                                            "bucca warranty timeline", "bucca 21 days",
                                            "bucca out of stock replacement", "bucca alter product",
                                            "bucca modified warranty", "bucca dealer warranty claim",
                                            "bucca defective product", "bucca broken swimbait",
                                            "bucca product broke", "bucca warranty coverage",
                                            "bucca warranty process", "bucca warranty steps",
                                            "bucca authorized retailer warranty",
                                            "receipt", "void warranty",
                                            "altered", "tampered", "21 days",
                                            "lure broke", "trick shad broke", "swimbait broke",
                                            # Natural-language phrase matches
                                            "have a warranty", "warranty claim", "out of stock",
                                            "void the warranty", "bucca brand lure", "file a warranty",
                                            "bucca brand warranty claim", "dealer process",
                                            "bucca brand", "warranty replacement"],
    "fishing/bucca-brand-returns.md":      ["bucca return", "bucca refund", "30 day return",
                                            "baits.com return", "return request", "free return",
                                            "pre-paid label", "21 business days", "original packaging",
                                            "bucca can return", "bucca send back",
                                            "bucca return refund", "bucca return window",
                                            "bucca 30 days", "bucca refund method",
                                            "bucca refund timeline", "bucca shipping refundable",
                                            "bucca prepaid label", "bucca dealer return",
                                            "bucca tackle shop return", "bucca used return",
                                            "bucca store credit", "bucca exchange",
                                            "bucca changed mind", "bucca return original packaging",
                                            "bucca return request baits", "bucca return eligibility",
                                            "bucca unused product return",
                                            "store credit", "prepaid label", "unused",
                                            "original packaging", "changed mind",
                                            "money back", "21 business days",
                                            # Natural-language phrase matches
                                            "return a bucca", "bucca brand refund",
                                            "return my bucca", "bucca brand return",
                                            "send it back", "return shipping free",
                                            "tackle shop return", "used bucca"],
    "fishing/bonehead-tackle-carbon-fiber-spinning-rods.md": [
                                        "bonehead carbon fiber", "bonehead spinning rod", "bonehead rod",
                                        "carbon fiber spinning", "green spinning rod", "neon green rod",
                                        "bonehead 10 foot", "bonehead 8 foot", "bonehead 7 foot",
                                        "bonehead 5 foot", "premium bonehead", "bonehead rod price",
                                        "CF rod", "CF series", "CF rods", "the CF", "about CF",
                                        "CF and e-series", "CF vs e-series", "difference CF",
                                        "carbon fiber series", "premium series", "premium rod"],
    "fishing/bonehead-tackle-e-series-carbon-fiber-spinning-rods.md": [
                                        "e-series", "e series", "bonehead e-series", "e-series carbon",
                                        "e-series rod", "e-series spinning", "e series spinning",
                                        "budget bonehead", "affordable bonehead",
                                        "bonehead 12 foot", "e-series 12 foot", "e-series 10 foot"],
    "fishing/bonehead-tackle-carbon-fiber-replacement-tips.md": [
                                        "replacement tip", "rod tip replacement", "bonehead replacement tip",
                                        "e-series replacement tip", "carbon fiber replacement tip",
                                        "broken rod tip", "tip section", "bonehead tip", "replace my tip",
                                        "bonehead 12 foot replacement", "damaged tip"],
    "fishing/bonehead-tackle-warranty.md": [
                                        "bonehead warranty", "bonehead limited warranty",
                                        "90 day warranty", "1 year warranty", "one year warranty",
                                        "bonehead defect", "manufacturing defect bonehead",
                                        "bonehead replacement fee", "30 dollar replacement",
                                        "bonehead warranty claim", "broken bonehead rod",
                                        "bonehead support", "e-series warranty", "carbon fiber warranty"],
    # "fishing/dealer-inquiry.md":      ["dealer", "wholesale", "distributor", "territory", "credit app"],   # 2026-05-21: page not yet created
    # "fishing/fishing-overview.md":    ["fishing", "rod", "reel", "lure", "bait", "trout", "bass", "spoon"],  # 2026-05-21: page not yet created
    "hunting/feeders-and-timers.md":  ["feeder", "timer", "boss buck", "wgi", "wildgame", "th-270",
                                       "battery", "spinner", "dispense", "motor", "jam", "error"],
    "hunting/avian-x.md":             ["avian", "avian-x", "decoy", "blind", "lcd valve"],
    "hunting/replacement-parts.md":   ["muddy", "hawk", "replacement part", "stand", "ladder", "cable",
                                       "bloodsport", "outsert"],
    "hunting/sog-knives.md":          ["sog", "knife", "knives", "cutlery", "blade", "engraving"],
    "hunting/product-comparisons.md": ["compare", "comparison", "versus", "vs", "difference between", "better"],
    "hunting/procedures.md":          ["bass pro", "cabela", "bc ", "unit of measure", "procedure"],
    "hunting/box-blinds.md":          ["box blind", "hunting blind", "blind panel"],
    "hunting/walkers.md":             ["walker", "ear protection", "game ear", "hearing", "nrr"],
    "wireless/connect-cellular.md":   ["connect", "connect cellular", "maneuver", "led", "red light",
                                       "sd card", "sim card", "antenna", "sync button", "data plan"],
    "wireless/muddy-mtrx.md":         ["mtrx", "muddy mtrx", "mud-mtrx"],
    "wireless/stealth-cam.md":        ["stealth cam", "stc-ds4k", "ds4ktm", "stealthcam"],
    "wireless/wireless-overview.md":  ["camera", "cellular camera", "trail cam", "app", "account",
                                       "at&t", "verizon", "signal", "solar panel", "connector"],
    # "agent-operations.md":            ["hours", "holiday", "schedule", "contact", "phone", "email",
    #                                    "support hours", "8 am", "cst"],  # 2026-05-21: page not yet created
}


def _get_wiki_pages() -> dict:
    """Import WIKI_PAGES from ingest_customer.py — single source of truth."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ingest_customer", BASE_DIR / "ingest_customer.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "WIKI_PAGES", {})
    except Exception:
        return {}


def _build_base_keywords() -> Dict[str, List[str]]:
    """
    Build keyword map from WIKI_PAGES in ingest_customer.py.
    Falls back to PAGE_KEYWORDS_BASE if import fails.
    """
    pages = _get_wiki_pages()
    if not pages:
        return {k: list(v) for k, v in PAGE_KEYWORDS_BASE.items()}

    kw_map: Dict[str, List[str]] = {}
    for rel, defn in pages.items():
        kws: list[str] = []
        for brand in defn.get("brands", []):
            if brand:
                kws.append(brand.lower())
        for topic in defn.get("topics", []):
            if topic:
                kws.append(topic.lower())
        if kws:
            kw_map[rel] = kws
    return kw_map


# ── Keyword supplements (written by auto-ingest approve) ─────────────────────

def _load_supplements() -> Dict[str, List[str]]:
    if not SUPPLEMENTS_PATH.exists():
        return {}
    try:
        data = json.loads(SUPPLEMENTS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_supplements(data: Dict[str, List[str]]) -> None:
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    SUPPLEMENTS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def merge_page_keywords() -> Dict[str, List[str]]:
    """Return merged keywords: ingest_customer.py registry + runtime supplements."""
    merged: Dict[str, List[str]] = _build_base_keywords()
    for path, extras in _load_supplements().items():
        if not isinstance(extras, list):
            continue
        merged.setdefault(path, [])
        seen_lower = {x.lower() for x in merged[path]}
        for x in extras:
            if not isinstance(x, str):
                continue
            xl = x.strip().lower()
            if xl and xl not in seen_lower:
                merged[path].append(x.strip())
                seen_lower.add(xl)
    return merged


def append_keyword_supplements(rel_path: str, brands: list[str],
                                topics: list[str]) -> None:
    """Merge brands/topics into supplements for one wiki path (deduped)."""
    rel_path = rel_path.replace("\\", "/").lstrip("/")
    extras = [b for b in brands  if isinstance(b, str) and b.strip()]
    extras += [t for t in topics if isinstance(t, str) and t.strip()]
    if not extras:
        return
    data = _load_supplements()
    cur  = list(data.get(rel_path, []))
    seen = {x.lower() for x in cur}
    for x in extras:
        xl = x.strip().lower()
        if xl and xl not in seen:
            cur.append(x.strip())
            seen.add(xl)
    data[rel_path] = cur
    save_supplements(data)


# ─────────────────────────────────────────────────────────────────────────────
# Keyword scoring
# ─────────────────────────────────────────────────────────────────────────────

def _extract_body(content: str) -> str:
    if not content.strip().startswith("---"):
        return content
    end = content.find("\n---\n", 3)
    return content[end + 5:] if end != -1 else content


def score_wiki_pages(
    query: str,
    history_text: str,
    wiki_cache: dict[str, str],
    keywords: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, int]:
    """
    Score each page by keyword matching.

    Weights:
      +5  brand-name match in query
      +3  topic keyword match in query
      +2  keyword also appears in page body (confirms relevance)
      +1  keyword in recent conversation history only
    """
    if keywords is None:
        keywords = merge_page_keywords()

    q_lower     = query.lower()
    search_text = (query + " " + history_text[-500:]).lower()
    wiki_pages  = _get_wiki_pages()

    scores: Dict[str, int] = {}
    for rel in wiki_cache:
        if rel in SKIP_PAGES:
            continue
        score    = 0
        page_def = wiki_pages.get(rel, {})
        brands   = {b.lower() for b in page_def.get("brands", [])}
        body     = _extract_body(wiki_cache[rel]).lower()

        for kw in keywords.get(rel, []):
            kw_l     = kw.lower()
            is_brand = kw_l in brands

            if kw_l in q_lower:
                score += 5 if is_brand else 3
                if kw_l in body:
                    score += 2
            elif kw_l in search_text:
                score += 1

        scores[rel] = score

    return scores


# ─────────────────────────────────────────────────────────────────────────────
# Page selection
# ─────────────────────────────────────────────────────────────────────────────

def select_ranked_pages(
    scores: dict,
    wiki_cache: Dict[str, str],
    max_pages: int = 4,
    query: str = "",
) -> List[tuple]:
    """
    Rank pages by score. Three rules:
    1. Brand gate — if query names a brand, zero out other brands' pages.
    2. Overview demotion — if any brand page scored > 0, cap overviews at 1.
    3. Fallback — if nothing scored, return category overviews.
    """
    # ── Brand gate ────────────────────────────────────────────────────────────
    # Maps brand name (as it appears in queries) to page path prefixes it owns.
    # If a query names a brand, pages from OTHER brands are zeroed out.
    BRAND_PREFIXES = {
        "phenix":   "fishing/phenix-rods",
        "dobyns":   "fishing/dobyns",
        "bonehead": "fishing/bonehead-tackle",
        "bucca":    "fishing/bucca-brand",
    }
    q_lower = query.lower()
    named_brands = [brand for brand in BRAND_PREFIXES if brand in q_lower]

    # When no explicit brand word is present, detect brand via product-model signals.
    if not named_brands:
        _PHENIX_MODEL_SIGNALS = {
            "abyss", "abyss hd", "black diamond", "pandora", "rts inshore",
            "m1 inshore", "megalodon", "titan slow", "titan popping",
            "trifecta", "cicada", "feather rod", "iron feather", "elixir",
            "mirage", "dragonfly", "redeye", "kokanee reaper", "maxim",
            "recon elite", "crankbait xg", "black chrome", "ultra mbx",
            "ultra swimbait", "no-hassle", "boron legacy", "america no-hassle",
        }
        _DOBYNS_MODEL_SIGNALS = {
            "champion extreme", "champion xp", "xtasy", "sierra micro",
            "fury rod", "colt rod", "kaden rod", "maverick reel",
            "dobyns fury", "dobyns colt", "the fury", "the colt",
            "fury and", "and the colt", "fury or", "colt or",
        }
        _BUCCA_MODEL_SIGNALS = {
            "trick shad", "baby bull shad", "bull mullet", "bull wake",
            "baby bull gill", "baby bull rat", "lil baby bull",
        }
        _BONEHEAD_MODEL_SIGNALS = {
            "e-series rod", "e series rod", "bonehead carbon", "bonehead spinning",
            "bonehead rod", "bonehead tip", "carbon fiber replacement tip",
            "e-series replacement", "bonehead replacement",
        }
        if any(sig in q_lower for sig in _PHENIX_MODEL_SIGNALS):
            named_brands = ["phenix"]
        elif any(sig in q_lower for sig in _DOBYNS_MODEL_SIGNALS):
            named_brands = ["dobyns"]
        elif any(sig in q_lower for sig in _BUCCA_MODEL_SIGNALS):
            named_brands = ["bucca"]
        elif any(sig in q_lower for sig in _BONEHEAD_MODEL_SIGNALS):
            named_brands = ["bonehead"]

    if named_brands:
        # Keep only pages that belong to a named brand or are overviews/operations
        allowed_prefixes = tuple(
            BRAND_PREFIXES[b] for b in named_brands
        )
        gated: dict = {}
        for rel, s in scores.items():
            is_overview = rel in OVERVIEW_PAGES or not rel.startswith("fishing/")
            is_allowed  = rel.startswith(allowed_prefixes)
            gated[rel]  = s if (is_allowed or is_overview) else 0.0
        scores = gated

    # ── Category gate ─────────────────────────────────────────────────────────
    # Blocks cross-category semantic noise.
    # fishing query → zero out hunting/ and wireless/ pages
    # hunting query → zero out fishing/ pages
    FISHING_SIGNALS = [
        "rod", "rods", "fishing", "fish", "reel", "lure", "bait", "braid",
        "saltwater", "freshwater", "bass", "trout", "salmon", "steelhead",
        "tuna", "inshore", "pelagic", "abyss", "phenix", "dobyns", "bonehead",
        "bucca", "trick shad", "baby bull", "bull mullet", "bull wake",
        "cicada", "feather", "elixir", "swimbait", "jigging", "popping",
        "ultralight", "crankbait", "walleye", "kokanee", "warranty", "tier fee",
        "travel rod", "live bait", "snapped", "broken rod",
    ]
    HUNTING_SIGNALS = [
        "feeder", "decoy", "blind", "stand", "knife", "trail cam",
        "camera", "ear protection", "walker", "avian", "sog", "muddy",
        "boss buck", "wildgame", "wgi",
    ]
    is_fishing = any(sig in q_lower for sig in FISHING_SIGNALS)
    is_hunting = any(sig in q_lower for sig in HUNTING_SIGNALS)

    if is_fishing and not is_hunting:
        scores = {
            rel: (s if not rel.startswith("hunting/") and
                  not rel.startswith("wireless/") else 0.0)
            for rel, s in scores.items()
        }
    elif is_hunting and not is_fishing:
        scores = {
            rel: (s if not rel.startswith("fishing/") else 0.0)
            for rel, s in scores.items()
        }

    brand_max = max(
        (s for rel, s in scores.items() if rel not in OVERVIEW_PAGES),
        default=0,
    )

    adjusted: dict = {}
    for rel, s in scores.items():
        if rel in OVERVIEW_PAGES and brand_max > 0:
            adjusted[rel] = min(float(s), 1.0)
        else:
            adjusted[rel] = float(s)

    ranked = sorted(
        [(rel, s) for rel, s in adjusted.items() if s > 0 and rel in wiki_cache],
        key=lambda x: -x[1],
    )[:max_pages]

    if not ranked:
        fallbacks = [
            # ("fishing/fishing-overview.md",  0),   # 2026-05-21: page not yet created
            # ("wireless/wireless-overview.md", 0),  # 2026-05-21: page not yet created
            # ("hunting/hunting-overview.md",   0),  # 2026-05-21: page not yet created
        ]
        ranked = [(r, s) for r, s in fallbacks if r in wiki_cache][:2]

    return ranked


# ─────────────────────────────────────────────────────────────────────────────
# Section extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_relevant_sections(
    content: str,
    query: str,
    max_chars: int = 5000,
) -> str:
    """
    Extract only the ## sections most relevant to the query.
    Keeps frontmatter. Fills budget with highest-scoring sections.
    Always includes the primary procedure section if present.
    """
    if len(content) <= max_chars:
        return content

    body    = _extract_body(content)
    pattern = re.compile(r"(?=^## )", re.MULTILINE)
    parts   = pattern.split(body)

    fm_end   = content.find("\n---\n", 3)
    fm_block = content[:fm_end + 5] if fm_end != -1 else ""

    q_lower = query.lower()
    q_words = set(w for w in re.split(r"\W+", q_lower) if len(w) > 2)

    # Score each section
    scored: list[tuple[int, str]] = []
    for section in parts:
        if not section.strip():
            continue
        s_lower = section.lower()
        score   = sum(1 for w in q_words if w in s_lower)
        for line in section.splitlines():
            if line.startswith("|"):
                score += sum(2 for w in q_words if w in line.lower())
        scored.append((score, section))

    scored.sort(key=lambda x: -x[0])

    # Pin procedure and tier table sections regardless of score
    procedure_sec   = None
    complete_rods   = None
    blanks_sec      = None
    for section in parts:
        heading = section.splitlines()[0].lower() if section.strip() else ""
        if procedure_sec is None and any(
            kw in heading for kw in
            ["program 1", "no-hassle", "how to submit", "replacement claim"]
        ):
            procedure_sec = section
        if complete_rods is None and "complete rods" in heading:
            complete_rods = section
        if blanks_sec is None and "blank" in heading and "section" in heading:
            blanks_sec = section

    pinned = {id(s) for s in [procedure_sec, complete_rods, blanks_sec] if s}
    remaining = [(sc, s) for sc, s in scored if id(s) not in pinned]

    budget   = max_chars - len(fm_block)
    selected: list[str] = []

    for pinned_sec in [procedure_sec, complete_rods, blanks_sec]:
        if pinned_sec and budget > 0:
            selected.append(pinned_sec)
            budget -= len(pinned_sec)

    for _, section in remaining:
        if budget <= 0:
            break
        if section not in selected:
            selected.append(section)
            budget -= len(section)

    # Restore document order
    doc_order = {s: i for i, s in enumerate(parts)}
    selected.sort(key=lambda s: doc_order.get(s, 999))

    result = fm_block + "\n".join(selected)
    if len(result) < len(content):
        result += f"\n\n[{len(content) - len(result)} chars from other sections omitted]"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main context builder — called by app.py
# ─────────────────────────────────────────────────────────────────────────────

def build_wiki_context(
    query: str,
    history_text: str,
    wiki_cache: Dict[str, str],
    # max_pages_sent: int = 4,  # old — tier-fees page ranked #6, got cut off behind product pricing sections
    # max_pages_sent: int = 6,  # old — tier-fees page ranked #8, still cut off for cheapest-replacement queries
    max_pages_sent: int = 8,
    max_wiki_tokens: int = 8000,  # bumped to accommodate 8 pages (was 6000)
    index_text: Optional[str] = None,
    brand_hint: Optional[str] = None,
) -> tuple[str, list[tuple[str, float]]]:
    """
    Build the wiki context block sent to the LLM.

    TWO MODES depending on query type:

    MODE 1 — SECTION-LEVEL (natural language queries, no strong keyword match):
      query → embed → score every ## section → pick top 10 sections directly
      → group by page → send best sections to LLM
      Example: "lightest rod for steelhead on 6 lb"
        → "Trifecta Lite" section scores 0.88 ← this is what LLM reads
        → freshwater-bass page never enters context (sections score 0.42)

    MODE 2 — PAGE-LEVEL HYBRID (strong keyword match, e.g. model numbers):
      query → keyword score + semantic score → pick top pages → extract sections
      Example: "PHX-CAX-C907" → keyword match on salmon page → page-level wins

    The threshold between modes: if max keyword score < KEYWORD_THRESHOLD,
    use section-level retrieval. Otherwise use page-level hybrid.
    """
    if not wiki_cache:
        return "Wiki is empty. Run ingest first.", []

    # KEYWORD_THRESHOLD = 9  # below this → section-level mode  [removed — always use section-level]

    # kw             = merge_page_keywords()                              # [moved to keyword-only fallback]
    # keyword_scores = score_wiki_pages(query, history_text, wiki_cache, kw)
    # max_kw_score   = max(keyword_scores.values()) if keyword_scores else 0

    # ── Detect query category for section filtering ────────────────────────────
    q_lower = query.lower()
    FISHING_SIGNALS = [
        "rod","rods","fishing","fish","reel","lure","bait","braid",
        "saltwater","freshwater","bass","trout","salmon","steelhead",
        "tuna","inshore","pelagic","abyss","phenix","dobyns","bonehead",
        "bucca","trick shad","baby bull","bull mullet","bull wake",
        "cicada","feather","elixir","swimbait","jigging","popping","ultralight",
        "crankbait","walleye","kokanee","warranty","tier fee","travel rod",
        "live bait","snapped","broken rod","yellowtail","largemouth",
    ]
    HUNTING_SIGNALS = [
        "feeder","decoy","blind","stand","knife","trail cam","camera",
        "ear protection","walker","avian","sog","muddy","boss buck","wildgame",
    ]
    is_fishing = any(sig in q_lower for sig in FISHING_SIGNALS)
    is_hunting = any(sig in q_lower for sig in HUNTING_SIGNALS)

    if is_fishing and not is_hunting:
        cat_filter = "fishing"
    elif is_hunting and not is_fishing:
        cat_filter = "hunting"
    else:
        cat_filter = None

    # ── Auto-detect single brand from query + recent history ─────────────────
    # Applies a brand filter to the vector store so Dobyns/Bucca sections are
    # never surfaced for queries that explicitly name Phenix (and vice versa).
    if brand_hint is None:
        _BRAND_FILTER_MAP = {
            "phenix":   "fishing/phenix-rods",
            "dobyns":   "fishing/dobyns",
            "bucca":    "fishing/bucca-brand",
            "bonehead": "fishing/bonehead-tackle",
        }
        # Priority 1 — current query only.
        # If the query explicitly names a brand, use it immediately.
        # This prevents history bleed: "Phenix" in prior answer + "Dobyns" in new
        # query used to produce two brands → no filter → wrong pages retrieved.
        _query_brands = [b for b in _BRAND_FILTER_MAP if b in q_lower]
        if len(_query_brands) == 1:
            brand_hint = _BRAND_FILTER_MAP[_query_brands[0]]

        # Priority 2 — fall back to history only when query has NO brand name.
        # Follow-up questions like "How much does it cost?" inherit brand from context.
        # _detect_text = q_lower + " " + history_text[-300:].lower()  # old: query+history combined
        if brand_hint is None:
            _detect_text = history_text[-300:].lower()
            _named_brands = [b for b in _BRAND_FILTER_MAP if b in _detect_text]
            if len(_named_brands) == 1:
                brand_hint = _BRAND_FILTER_MAP[_named_brands[0]]

        if brand_hint is None:
            # Priority 3 — model/product name signals in the query.
            # Only fires when no brand name is present.
            _detect_text = q_lower  # model signals are in the query, not history
            _PHENIX_MODEL_SIGNALS = {
                "abyss", "abyss hd", "black diamond", "pandora", "rts inshore",
                "m1 inshore", "megalodon", "titan slow", "titan popping",
                "trifecta", "cicada", "feather rod", "iron feather", "elixir",
                "mirage", "dragonfly", "redeye", "kokanee reaper", "maxim",
                "recon elite", "crankbait xg", "black chrome", "ultra mbx",
                "ultra swimbait", "no-hassle", "boron legacy", "america no-hassle",
            }
            _DOBYNS_MODEL_SIGNALS = {
                "champion extreme", "champion xp", "xtasy", "sierra micro",
                "fury rod", "colt rod", "kaden rod", "maverick reel",
                "dobyns fury", "dobyns colt", "the fury", "the colt",
                "fury and", "and the colt", "fury or", "colt or",
            }
            _BUCCA_MODEL_SIGNALS = {
                "trick shad", "baby bull shad", "bull mullet", "bull wake",
                "baby bull gill", "baby bull rat", "lil baby bull",
            }
            _BONEHEAD_MODEL_SIGNALS = {
                "e-series rod", "e series rod", "bonehead carbon", "bonehead spinning",
                "bonehead rod", "bonehead tip", "carbon fiber replacement tip",
                "e-series replacement", "bonehead replacement",
            }
            if any(sig in _detect_text for sig in _PHENIX_MODEL_SIGNALS):
                brand_hint = "fishing/phenix-rods"
            elif any(sig in _detect_text for sig in _DOBYNS_MODEL_SIGNALS):
                brand_hint = "fishing/dobyns-rods"
            elif any(sig in _detect_text for sig in _BUCCA_MODEL_SIGNALS):
                brand_hint = "fishing/bucca-brand"
            elif any(sig in _detect_text for sig in _BONEHEAD_MODEL_SIGNALS):
                brand_hint = "fishing/bonehead-tackle"

    # ── MODE 1: Section-level retrieval (always active) ───────────────────────
    # if _VECTOR_AVAILABLE and vector_store_ready() and max_kw_score < KEYWORD_THRESHOLD:
    if _VECTOR_AVAILABLE and vector_store_ready():
        # sections = get_top_sections(          # v1 — section-text embeddings only
        sections = get_top_sections_with_variants(  # v2 — section + question-variant hybrid
            _expand_query(query),
            # top_n=12,  # v2 — bumped to 20 to give reranker more candidates
            # top_n=20,  # v2 — bumped to 30 to capture low-scoring but specific sections (e.g. blanks fee)
            top_n=30,
            # min_score=0.60,  # v2 — too high: specific Q&A sections score 0.55–0.59, filtered before reranker
            min_score=0.55,    # v3 — lowered; reranker demotes irrelevant noise that sneaks in at 0.55–0.59
            # brand_hint (e.g. "fishing/phenix-rods") takes precedence over
            # category_filter ("fishing") — narrows to one brand's sections only
            category_filter=cat_filter,
            brand_filter=brand_hint,
        )
        if sections:
            # v3 — cross-encoder reranking: re-scores (query, section) pairs so
            # the most answer-relevant sections rise to the top regardless of
            # bi-encoder score.  Uses the ORIGINAL query (not expanded) so the
            # cross-encoder sees natural language rather than keyword-stuffed
            # expansions.  Falls back to bi-encoder order if unavailable.
            sections = rerank_sections(query, sections)
            context, ranked = build_context_from_sections(
                sections,
                max_chars=max_wiki_tokens,
                max_pages=max_pages_sent,
            )
            # Append index
            idx = index_text or (INDEX_PATH.read_text(encoding="utf-8")
                                  if INDEX_PATH.exists() else "")
            if idx:
                # context += f"\n\n=== WIKI INDEX ===\n{idx[:400]}"  # old: only first row visible
                context += f"\n\n=== WIKI INDEX ===\n{idx}"
            return context, ranked

    # ── Fallback: hybrid (vector + keyword) or keyword-only if vector unavailable ─
    kw             = merge_page_keywords()
    keyword_scores = score_wiki_pages(query, history_text, wiki_cache, kw)
    if _VECTOR_AVAILABLE and vector_store_ready():
        scores = hybrid_score_pages(
            _expand_query(query), history_text, wiki_cache,
            keyword_scores, brand_hint=query.split("\n")[0]
        )
    else:
        scores = {r: float(s) for r, s in keyword_scores.items()}

    # When a brand_hint is set (brand confirmed in session), filter fallback scores
    # to that brand's pages only — prevents keyword leakage to other brand pages.
    if brand_hint:
        scores = {r: s for r, s in scores.items() if r.startswith(brand_hint)}

    ranked = select_ranked_pages(scores, wiki_cache, max_pages_sent, query=query)

    parts: list[str] = []
    for rel, score in ranked:
        page_content = wiki_cache.get(rel, "")
        if not page_content:
            continue
        extracted = extract_relevant_sections(page_content, query, max_chars=max_wiki_tokens)
        parts.append(f"=== wiki/{rel} (relevance: {score:.1f}) ===\n{extracted}")

    idx = index_text or (INDEX_PATH.read_text(encoding="utf-8")
                          if INDEX_PATH.exists() else "")
    if idx:
        # parts.append(f"=== WIKI INDEX ===\n{idx[:400]}")  # old: only first row visible
        parts.append(f"=== WIKI INDEX ===\n{idx}")

    return "\n\n".join(parts), ranked


# ─────────────────────────────────────────────────────────────────────────────
# Helpers used by server.py and ingest_customer.py
# ─────────────────────────────────────────────────────────────────────────────

def extract_frontmatter_lists(content: str, key: str) -> list[str]:
    """Parse brands: [a, b] or topics: [x] from YAML frontmatter."""
    if not content.strip().startswith("---"):
        return []
    end = content.find("\n---\n", 3)
    if end == -1:
        return []
    fm  = content[3:end]
    out: list[str] = []
    bracket = re.search(
        rf"^{re.escape(key)}:\s*\[(.*?)\]\s*$", fm,
        re.MULTILINE | re.IGNORECASE
    )
    if bracket:
        for part in bracket.group(1).split(","):
            p = part.strip().strip('"\'')
            if p:
                out.append(p)
        return out
    in_list = False
    for line in fm.splitlines():
        if re.match(rf"^{re.escape(key)}:\s*$", line, re.IGNORECASE):
            in_list = True
            continue
        if in_list:
            m = re.match(r"^\s*-\s+(.+)$", line)
            if m:
                out.append(m.group(1).strip().strip('"\''))
            elif line.strip() and not line.startswith(" ") and ":" in line.split()[0]:
                break
    return out
