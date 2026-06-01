#!/usr/bin/env python3
"""
test_rag_dobyns_bucca.py — RAG retrieval evaluation for Dobyns Rods and Bucca Brand.

Tests customer-facing questions against the current wiki and checks:
  1. Is the expected source page retrieved? (source check)
  2. Is Phenix (or the other brand) being injected into answers? (cross-brand contamination)

Does NOT call the LLM — tests the retrieval layer only (fast, deterministic).

Question status codes:
  TBD     — First run, no baseline yet (will establish baseline)
  PASS    — Expected source reliably in top results
  PARTIAL — Expected source sometimes retrieved but not consistently
  FAIL    — Expected source rarely/never in top results
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from wiki_retrieval import build_wiki_context, score_wiki_pages, merge_page_keywords, SKIP_PAGES

WIKI_DIR = BASE_DIR / "wiki"

# ── Dobyns Questions ──────────────────────────────────────────────────────────
# Format: (q_num, expected_source_rel, baseline_result, question_text)

DOBYNS_QUESTIONS = [
    # Brand Overview — dobyns-rods.md
    (1,  "fishing/dobyns-rods.md",                  "PASS", "What is Dobyns Rods?"),
    (2,  "fishing/dobyns-rods.md",                  "PASS", "Who founded Dobyns Rods?"),
    (3,  "fishing/dobyns-rods.md",                  "PASS", "Does Dobyns make anything besides rods?"),
    (4,  "fishing/dobyns-rods.md",                  "PASS", "What is the most affordable Dobyns rod?"),
    (5,  "fishing/dobyns-rods.md",                  "PASS", "What is the best Dobyns rod for deep water sensitivity?"),
    (6,  "fishing/dobyns-rods.md",                  "PASS", "What is the difference between the Fury and the Colt?"),
    (7,  "fishing/dobyns-rods.md",                  "PASS", "What is the Xtasy rod made of?"),
    (8,  "fishing/dobyns-rods.md",                  "PASS", "Does Dobyns make crappie rods?"),
    (9,  "fishing/dobyns-rods.md",                  "PASS", "Does Dobyns make travel rods?"),
    # Q10: Mike Bucca is both a Bucca Brand designer and a Dobyns signature rod —
    #      keyword layer routes to Bucca Brand first; catalog IS retrieved (rank 2).
    (10, "fishing/dobyns-rods-product-catalog.md",  "PARTIAL", "What is the Mike Bucca BullShad rod?"),
    (11, "fishing/dobyns-rods-product-catalog.md",  "PASS", "What is the Josh Jones Hyperlite rod designed for?"),
    (12, "fishing/dobyns-rods.md",                  "PASS", "What is the difference between the Xtasy and the Champion Extreme HP?"),
    (13, "fishing/dobyns-rods.md",                  "PASS", "What is the Sierra Ultra Finesse designed for?"),
    (14, "fishing/dobyns-rods.md",                  "PASS", "Which Dobyns rod series is best for beginners?"),

    # Product Catalog — dobyns-rods-product-catalog.md
    (15, "fishing/dobyns-rods-product-catalog.md",  "PASS", "How many models does the Dobyns Champion XP come in?"),
    (16, "fishing/dobyns-rods-product-catalog.md",  "PASS", "What guides does the Dobyns Xtasy use?"),
    (17, "fishing/dobyns-rods-product-catalog.md",  "PASS", "What is the Dobyns Sierra Micro?"),
    (18, "fishing/dobyns-rods-product-catalog.md",  "PASS", "Does Dobyns make a swimbait rod?"),
    (19, "fishing/dobyns-rods-product-catalog.md",  "PASS", "What is the E.C. Special rod?"),
    (20, "fishing/dobyns-rods-product-catalog.md",  "PASS", "How long are the Josh Jones Hyperlite rods?"),
    (21, "fishing/dobyns-rods-product-catalog.md",  "PASS", "What is the Dobyns Kaden Travel rod?"),

    # Warranty — dobyns-rods-warranty.md
    (22, "fishing/dobyns-rods-warranty.md",         "PASS", "What warranty does my Dobyns rod have?"),
    (23, "fishing/dobyns-rods-warranty.md",         "PASS", "Does the Dobyns Colt have a limited lifetime warranty?"),
    (24, "fishing/dobyns-rods-warranty.md",         "PASS", "Does the Dobyns warranty cover accidental breakage?"),
    (25, "fishing/dobyns-rods-warranty.md",         "PASS", "My Dobyns rod broke within 60 days of purchase — is it covered?"),
    (26, "fishing/dobyns-rods-warranty.md",         "PASS", "Can I transfer my Dobyns warranty to another person?"),
    (27, "fishing/dobyns-rods-warranty.md",         "PASS", "I bought a used Dobyns rod — is it still under warranty?"),
    (28, "fishing/dobyns-rods-warranty.md",         "PASS", "What warranty does the Dobyns Maverick reel have?"),
    (29, "fishing/dobyns-rods-warranty.md",         "PASS", "What happens if my Dobyns rod model is discontinued?"),
    (30, "fishing/dobyns-rods-warranty.md",         "PASS", "How do I file a manufacturing defect claim for my Dobyns rod?"),

    # No-Hassle Replacement — dobyns-rods-replacement.md
    (31, "fishing/dobyns-rods-replacement.md",      "PASS", "My Dobyns rod broke — how do I get a replacement?"),
    (32, "fishing/dobyns-rods-replacement.md",      "PASS", "Do I need to ship my whole Dobyns rod in for replacement?"),
    (33, "fishing/dobyns-rods-replacement.md",      "PASS", "What is the Dobyns logo section?"),
    (34, "fishing/dobyns-rods-replacement.md",      "PASS", "Where do I cut my broken Dobyns rod?"),
    (35, "fishing/dobyns-rods-replacement.md",      "PASS", "Can I process my Dobyns replacement through a dealer?"),
    (36, "fishing/dobyns-rods-replacement.md",      "PASS", "Can I upgrade my Dobyns rod when I send it in for replacement?"),
    (37, "fishing/dobyns-rods-replacement.md",      "PASS", "How long does the Dobyns replacement process take?"),
    (38, "fishing/dobyns-rods-replacement.md",      "PASS", "Where do I ship my Dobyns warranty package?"),
    (39, "fishing/dobyns-rods-replacement.md",      "PASS", "How do I pay for my Dobyns replacement?"),

    # Tier Fees — dobyns-rods-tier-fees.md
    (40, "fishing/dobyns-rods-tier-fees.md",        "PASS", "How much does it cost to replace a Dobyns Colt?"),
    (41, "fishing/dobyns-rods-tier-fees.md",        "PASS", "How much does it cost to replace a Dobyns Xtasy?"),
    (42, "fishing/dobyns-rods-tier-fees.md",        "PASS", "How much does it cost to replace a Dobyns Fury?"),
    (43, "fishing/dobyns-rods-tier-fees.md",        "PASS", "How much does it cost to replace a Dobyns Champion Extreme?"),
    (44, "fishing/dobyns-rods-tier-fees.md",        "PASS", "What is the return shipping fee for a Dobyns replacement?"),
    (45, "fishing/dobyns-rods-tier-fees.md",        "PASS", "How is the Dobyns warranty upgrade fee calculated?"),
    (46, "fishing/dobyns-rods-tier-fees.md",        "PASS", "Do I have to pay sales tax on my Dobyns replacement?"),
    (47, "fishing/dobyns-rods-tier-fees.md",        "PASS", "Can I get international shipping for a Dobyns replacement?"),

    # Rod Care — dobyns-rods-care.md
    (48, "fishing/dobyns-rods-care.md",             "PASS", "How do I clean my Dobyns rod guides?"),
    (49, "fishing/dobyns-rods-care.md",             "PASS", "How do I care for the cork grip on my Dobyns rod?"),
    (50, "fishing/dobyns-rods-care.md",             "PASS", "How should I store my Dobyns rod?"),
    (51, "fishing/dobyns-rods-care.md",             "PASS", "Can I get a free replacement tip section for my Dobyns rod?"),
    (52, "fishing/dobyns-rods-care.md",             "PASS", "How do I request free replacement guides for my Dobyns rod?"),

    # Reels & Combos — dobyns-reels-combos.md
    (53, "fishing/dobyns-reels-combos.md",          "PASS", "What is the Maverick reel?"),
    (54, "fishing/dobyns-reels-combos.md",          "PASS", "What gear ratios does the Dobyns Maverick casting reel come in?"),
    (55, "fishing/dobyns-reels-combos.md",          "PASS", "Does Dobyns sell rod-and-reel combos?"),
    (56, "fishing/dobyns-reels-combos.md",          "PASS", "What sizes does the Maverick spinning reel come in?"),
    (57, "fishing/dobyns-reels-combos.md",          "PASS", "How many Maverick combo SKUs are available?"),

    # Lures & Terminal Tackle — dobyns-lures.md
    (58, "fishing/dobyns-lures.md",                 "PASS", "What spinnerbaits does Dobyns make?"),
    (59, "fishing/dobyns-lures.md",                 "PASS", "What is the difference between the D-Blade Advantage and the D-Blade Beast?"),
    (60, "fishing/dobyns-lures.md",                 "PASS", "Does Dobyns make jigs?"),
    (61, "fishing/dobyns-lures.md",                 "PASS", "What jig hook does the Dobyns Extreme Football Jig use?"),
    (62, "fishing/dobyns-lures.md",                 "PASS", "Does Dobyns make swimbait heads?"),

    # Semantic / NL variations — Dobyns
    (63, "fishing/dobyns-rods-warranty.md",         "PASS", "My Dobyns stick snapped in half — what should I do?"),
    (64, "fishing/dobyns-rods.md",                  "PASS", "Looking for a good Dobyns rod on a budget"),
    (65, "fishing/dobyns-rods.md",                  "PASS", "Need a Dobyns rod for livescope crappie"),
    (66, "fishing/dobyns-rods-replacement.md",      "PASS", "Can I swap my broken Dobyns for a better model?"),
    (67, "fishing/dobyns-rods-warranty.md",         "PASS", "Dobyns rod snapped first time out — is it defective?"),
    (68, "fishing/dobyns-rods-tier-fees.md",        "PASS", "What will it cost me to replace my Dobyns Kaden?"),
]

# ── Bucca Brand Questions ─────────────────────────────────────────────────────

BUCCA_QUESTIONS = [
    # Brand Overview — bucca-brand.md
    (1,  "fishing/bucca-brand.md",                  "PASS", "What is Bucca Brand?"),
    (2,  "fishing/bucca-brand.md",                  "PASS", "Who designs Bucca Brand swimbaits?"),
    (3,  "fishing/bucca-brand.md",                  "PASS", "What type of plastic are Bucca Brand baits made from?"),
    (4,  "fishing/bucca-brand.md",                  "PASS", "Does Bucca Brand make saltwater lures?"),
    (5,  "fishing/bucca-brand.md",                  "PASS", "What is the Bull Mullet?"),
    (6,  "fishing/bucca-brand.md",                  "PASS", "Does Bucca Brand make soft plastic baits?"),
    (7,  "fishing/bucca-brand.md",                  "PASS", "Where can I buy Bucca Brand swimbaits?"),

    # Product Catalog — bucca-brand-product-catalog.md
    (8,  "fishing/bucca-brand-product-catalog.md",  "PASS", "What sizes does the Trick Shad come in?"),
    (9,  "fishing/bucca-brand-product-catalog.md",  "PASS", "What is the difference between the Trick Shad and the Baby Bull Shad?"),
    (10, "fishing/bucca-brand-product-catalog.md",  "PASS", "What is the Baby Bull Gill designed for?"),
    (11, "fishing/bucca-brand-product-catalog.md",  "PASS", "Does Bucca Brand make a topwater lure?"),
    (12, "fishing/bucca-brand-product-catalog.md",  "PASS", "What is the Buzzing Baby Bull Shad?"),
    (13, "fishing/bucca-brand-product-catalog.md",  "PASS", "What is the Baby Bull Rat?"),
    (14, "fishing/bucca-brand-product-catalog.md",  "PASS", "What is the Weedless Baby Bull Shad?"),
    (15, "fishing/bucca-brand-product-catalog.md",  "PASS", "Can I buy replacement tails for the 6 inch Trick Shad?"),
    (16, "fishing/bucca-brand-product-catalog.md",  "PASS", "Can I buy replacement tails for the Baby Bull Rat?"),
    (17, "fishing/bucca-brand-product-catalog.md",  "PASS", "What colors does the Baby Bull Gill come in?"),
    (18, "fishing/bucca-brand-product-catalog.md",  "PASS", "What is the weight of the Bull Mullet 8 inch?"),
    (19, "fishing/bucca-brand-product-catalog.md",  "PASS", "What hooks does the Bull Mullet use?"),
    (20, "fishing/bucca-brand-product-catalog.md",  "PASS", "What Bucca Brand swimbait should I use for bass?"),

    # Warranty — bucca-brand-warranty.md
    (21, "fishing/bucca-brand-warranty.md",         "PASS", "Does Bucca Brand have a warranty?"),
    (22, "fishing/bucca-brand-warranty.md",         "PASS", "How do I file a warranty claim for a Bucca Brand product?"),
    (23, "fishing/bucca-brand-warranty.md",         "PASS", "Do I need a receipt for a Bucca Brand warranty claim?"),
    (24, "fishing/bucca-brand-warranty.md",         "PASS", "What is a Return Authorization number?"),
    (25, "fishing/bucca-brand-warranty.md",         "PASS", "Can I ship my defective Bucca Brand product before getting an RA number?"),
    (26, "fishing/bucca-brand-warranty.md",         "PASS", "How long does a Bucca Brand warranty replacement take?"),
    (27, "fishing/bucca-brand-warranty.md",         "PASS", "What if my exact Bucca Brand item is out of stock when I file a claim?"),
    (28, "fishing/bucca-brand-warranty.md",         "PASS", "Does altering my Bucca Brand lure void the warranty?"),
    (29, "fishing/bucca-brand-warranty.md",         "PASS", "Can a dealer process my Bucca Brand warranty claim for me?"),

    # Returns — bucca-brand-returns.md
    (30, "fishing/bucca-brand-returns.md",          "PASS", "Can I return a Bucca Brand product for a refund?"),
    (31, "fishing/bucca-brand-returns.md",          "PASS", "How long do I have to return a Bucca Brand product?"),
    (32, "fishing/bucca-brand-returns.md",          "PASS", "How will I receive my Bucca Brand refund?"),
    (33, "fishing/bucca-brand-returns.md",          "PASS", "How long does a Bucca Brand refund take?"),
    (34, "fishing/bucca-brand-returns.md",          "PASS", "Are shipping costs refundable on a Bucca Brand return?"),
    (35, "fishing/bucca-brand-returns.md",          "PASS", "Is return shipping free for Bucca Brand?"),
    (36, "fishing/bucca-brand-returns.md",          "PASS", "Can I return a Bucca Brand product I bought from a tackle shop?"),
    (37, "fishing/bucca-brand-returns.md",          "PASS", "Can I return a used Bucca Brand swimbait?"),
    (38, "fishing/bucca-brand-returns.md",          "PASS", "Can I get store credit instead of a refund for a Bucca Brand return?"),

    # Semantic / NL variations — Bucca
    (39, "fishing/bucca-brand-product-catalog.md",  "PASS", "What Bucca swimbait works best around weeds?"),
    (40, "fishing/bucca-brand-product-catalog.md",  "PASS", "Best Bucca bait for big bass in saltwater?"),
    (41, "fishing/bucca-brand-warranty.md",         "PASS", "My Trick Shad broke — what do I do?"),
    (42, "fishing/bucca-brand-returns.md",          "PASS", "I changed my mind about my Bucca order — how do I send it back?"),
    (43, "fishing/bucca-brand-product-catalog.md",  "PASS", "Need a Bucca topwater bait for shallow water bass"),
    (44, "fishing/bucca-brand-product-catalog.md",  "PASS", "What size Trick Shad should I throw for big bass?"),
]

# ── Cross-brand contamination sets ───────────────────────────────────────────

PHENIX_PAGES = {
    "fishing/phenix-rods.md",
    "fishing/phenix-rods-saltwater-pelagic.md",
    "fishing/phenix-rods-saltwater-inshore.md",
    "fishing/phenix-rods-freshwater-bass.md",
    "fishing/phenix-rods-freshwater-specialty.md",
    "fishing/phenix-rods-trout-ultralight.md",
    "fishing/phenix-rods-salmon-steelhead.md",
    "fishing/phenix-rods-travel.md",
    "fishing/phenix-rods-warranty.md",
    "fishing/phenix-rods-tier-fees.md",
    "fishing/phenix-rods-pricing.md",
}

DOBYNS_PAGES = {
    "fishing/dobyns-rods.md",
    "fishing/dobyns-rods-product-catalog.md",
    "fishing/dobyns-rods-warranty.md",
    "fishing/dobyns-rods-replacement.md",
    "fishing/dobyns-rods-tier-fees.md",
    "fishing/dobyns-rods-care.md",
    "fishing/dobyns-reels-combos.md",
    "fishing/dobyns-lures.md",
}

BUCCA_PAGES = {
    "fishing/bucca-brand.md",
    "fishing/bucca-brand-product-catalog.md",
    "fishing/bucca-brand-warranty.md",
    "fishing/bucca-brand-returns.md",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_wiki_cache():
    cache = {}
    for page in WIKI_DIR.rglob("*.md"):
        rel = str(page.relative_to(WIKI_DIR)).replace("\\", "/")
        if rel not in SKIP_PAGES:
            cache[rel] = page.read_text()
    return cache


def get_top_pages(query: str, wiki_cache: dict, keywords: dict, n: int = 6) -> list[tuple[str, int]]:
    scores = score_wiki_pages(query, "", wiki_cache, keywords)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(p, s) for p, s in ranked if s > 0][:n]


SEP = "─" * 72


def run_suite(
    suite_name: str,
    questions: list,
    own_pages: set,
    contaminant_pages: dict[str, set],
    wiki_cache: dict,
    keywords: dict,
):
    print()
    print(SEP)
    print(f"{suite_name} — RAG Retrieval Evaluation")
    print(SEP)

    results = {
        "source_ok":      [],
        "source_missing": [],
        "contaminated":   [],
        "cont_present":   [],
    }

    # Flatten all contaminant pages
    all_contaminant = set()
    for s in contaminant_pages.values():
        all_contaminant.update(s)

    print(f"{'Q#':<5} {'Baseline':<9} {'Src?':<6} {'Contamination?':<18} {'Top Retrieved Pages'}")
    print(SEP)

    for (qnum, expected_src, baseline, question) in questions:
        top      = get_top_pages(question, wiki_cache, keywords, n=6)
        top_pages = [p for p, _ in top]

        src_ok = expected_src in top_pages

        contaminating = [p for p in top_pages if p in all_contaminant]

        contaminated_above = False
        if contaminating and src_ok:
            exp_rank   = top_pages.index(expected_src)
            cont_ranks = [top_pages.index(p) for p in contaminating]
            contaminated_above = any(r < exp_rank for r in cont_ranks)

        entry = {
            "q":            qnum,
            "baseline":     baseline,
            "question":     question[:60],
            "expected":     expected_src,
            "top_pages":    top_pages[:5],
            "contaminating": contaminating,
        }

        if not src_ok:
            results["source_missing"].append(entry)
        else:
            results["source_ok"].append(entry)

        if contaminated_above:
            results["contaminated"].append(entry)
        elif contaminating:
            results["cont_present"].append(entry)

        src_sym  = "✅" if src_ok else "❌"
        cont_sym = ""
        if contaminated_above:
            cont_sym = "🔴 ABOVE SRC"
        elif contaminating:
            cont_sym = "🟡 present"

        short = ", ".join(p.split("/")[-1].replace(".md", "") for p in top_pages[:4])
        print(f"Q{qnum:<4} {baseline:<9} {src_sym:<6} {cont_sym:<18} {short}")

    # Summary
    print()
    print(SEP)
    print("SUMMARY")
    print(SEP)
    total      = len(questions)
    ok         = len(results["source_ok"])
    miss       = len(results["source_missing"])
    cont_above = len(results["contaminated"])
    cont_any   = len(results["cont_present"])

    print(f"Total questions tested         : {total}")
    print(f"Expected source retrieved      : {ok}/{total}  {'✅' if miss == 0 else f'⚠️  {miss} misses'}")
    print(f"Cross-brand ranked above source: {cont_above}  {'✅' if cont_above == 0 else '🔴 CONTAMINATION'}")
    print(f"Cross-brand present (not above): {cont_any}  {'✅' if cont_any == 0 else '🟡 monitor'}")

    if results["source_missing"]:
        print()
        print("❌ SOURCE MISSES:")
        for e in results["source_missing"]:
            print(f"  Q{e['q']} [{e['baseline']}] {e['question']}")
            print(f"       Expected : {e['expected']}")
            print(f"       Got      : {', '.join(p.split('/')[-1] for p in e['top_pages'][:4])}")

    if results["contaminated"]:
        print()
        print("🔴 CROSS-BRAND CONTAMINATION (foreign brand ranked ABOVE expected source):")
        for e in results["contaminated"]:
            print(f"  Q{e['q']} [{e['baseline']}] {e['question']}")
            print(f"       Contaminant: {', '.join(e['contaminating'])}")
            print(f"       Top pages  : {', '.join(p.split('/')[-1] for p in e['top_pages'][:4])}")

    if results["cont_present"]:
        print()
        print("🟡 CROSS-BRAND PRESENT (not above source, but still in top results):")
        for e in results["cont_present"]:
            print(f"  Q{e['q']} {e['question']}")
            print(f"       Contaminant: {', '.join(c.split('/')[-1] for c in e['contaminating'])}")

    verdict = "✅ CLEAN" if miss == 0 and cont_above == 0 else \
              "🟡 MOSTLY CLEAN" if miss == 0 else \
              f"🔴 {miss} source miss(es), {cont_above} contamination(s)"
    print()
    print(f"VERDICT: {verdict}")
    return results


def main():
    wiki_cache = load_wiki_cache()
    keywords   = merge_page_keywords()

    print(SEP)
    print("GSM Outdoors — Dobyns + Bucca RAG Retrieval Evaluation")
    print(SEP)
    print(f"Wiki pages loaded : {len(wiki_cache)}")
    print(f"Keyword entries   : {len(keywords)}")

    dobyns_contaminants = {"Phenix": PHENIX_PAGES, "Bucca": BUCCA_PAGES}
    bucca_contaminants  = {"Phenix": PHENIX_PAGES, "Dobyns": DOBYNS_PAGES}

    run_suite(
        "DOBYNS RODS",
        DOBYNS_QUESTIONS,
        DOBYNS_PAGES,
        dobyns_contaminants,
        wiki_cache,
        keywords,
    )

    run_suite(
        "BUCCA BRAND",
        BUCCA_QUESTIONS,
        BUCCA_PAGES,
        bucca_contaminants,
        wiki_cache,
        keywords,
    )

    print()
    print(SEP)
    print("Run complete — update baseline_result values from TBD → PASS/PARTIAL/FAIL")
    print("after reviewing the output above.")
    print(SEP)


if __name__ == "__main__":
    main()
