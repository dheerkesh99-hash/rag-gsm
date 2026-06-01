#!/usr/bin/env python3
"""
test_rag_regression.py — Retrieval regression check after adding Dobyns & Bucca brands.

Tests all 48 active Phenix questions from RAG_Evaluated_v5.xlsx against the
current wiki (now including Dobyns + Bucca) and checks:
  1. Is the expected Phenix source page still retrieved? (source regression)
  2. Are Dobyns or Bucca pages being injected into Phenix answers? (cross-brand contamination)
  3. What changed vs the baseline evaluation?

Does NOT call the LLM — tests the retrieval layer only (fast, deterministic).
"""
import sys
import json
from pathlib import Path

# ── Questions from RAG_Evaluated_v5.xlsx (active, non-excluded) ───────────────
# Format: (q_num, expected_source_rel, original_result, question_text)
QUESTIONS = [
    (3,  "fishing/phenix-rods.md",                    "PASS",    "What are the four main Phenix rod categories and name two series each?"),
    (4,  "fishing/phenix-rods.md",                    "PASS",    "How long has Phenix been making rods?"),
    (5,  "fishing/phenix-rods.md",                    "PASS",    "Does Phenix make anything besides rods?"),
    (6,  "fishing/phenix-rods.md",                    "PASS",    "What materials does Phenix use in their rods?"),
    (7,  "fishing/phenix-rods-saltwater-pelagic.md",  "PASS",    "What is the best Phenix rod for live bait fishing?"),
    (8,  "fishing/phenix-rods-saltwater-pelagic.md",  "PASS",    "What is the difference between the Abyss and Abyss HD?"),
    (9,  "fishing/phenix-rods-saltwater-pelagic.md",  "PASS",    "What is the Black Diamond Hybrid made of?"),
    (10, "fishing/phenix-rods-saltwater-pelagic.md",  "PASS",    "Which Phenix rod is designed for female anglers?"),
    (11, "fishing/phenix-rods-saltwater-inshore.md",  "PASS",    "What is the RTS Inshore designed for?"),
    (12, "fishing/phenix-rods-saltwater-inshore.md",  "PASS",    "What Phenix rod should I use for slow pitch jigging?"),
    (13, "fishing/phenix-rods-saltwater-inshore.md",  "PARTIAL", "What is the best Phenix rod for tuna jigging?"),
    (14, "fishing/phenix-rods-saltwater-inshore.md",  "PASS",    "What is the difference between M1 Inshore and RTS Inshore?"),
    (15, "fishing/phenix-rods-freshwater-bass.md",    "PASS",    "What is the best Phenix rod for tournament bass fishing?"),
    (16, "fishing/phenix-rods-freshwater-bass.md",    "PASS",    "What is the best Phenix rod for big swimbait fishing?"),
    (17, "fishing/phenix-rods-freshwater-bass.md",    "PASS",    "What is the difference between the Feather and the M1?"),
    (18, "fishing/phenix-rods-freshwater-bass.md",    "FAIL",    "What lure weights does the M1 bass rod handle?"),
    (19, "fishing/phenix-rods-freshwater-specialty.md","PASS",   "What is the best Phenix crankbait rod?"),
    (20, "fishing/phenix-rods-freshwater-specialty.md","PASS",   "Does Phenix make a walleye rod?"),
    (21, "fishing/phenix-rods-freshwater-specialty.md","FAIL",   "What is the Kokanee Reaper designed for?"),
    (22, "fishing/phenix-rods-freshwater-specialty.md","PASS",   "What is the difference between Maxim and Recon Elite?"),
    (23, "fishing/phenix-rods-trout-ultralight.md",   "PASS",    "What is the best Phenix rod for panfish and crappie?"),
    (24, "fishing/phenix-rods-trout-ultralight.md",   "FAIL",    "What is the difference between the Iron Feather and the Elixir?"),
    (25, "fishing/phenix-rods-trout-ultralight.md",   "PASS",    "What line weight is the Iron Feather rated for?"),
    (26, "fishing/phenix-rods-trout-ultralight.md",   "PASS",    "Does the Elixir come in a telescopic version?"),
    (27, "fishing/phenix-rods-salmon-steelhead.md",   "PASS",    "What is the best Phenix rod for salmon around 9 feet?"),
    (28, "fishing/phenix-rods-salmon-steelhead.md",   "FAIL",    "What is the difference between the Trifecta and Trifecta Pro?"),
    (29, "fishing/phenix-rods-salmon-steelhead.md",   "PARTIAL", "What is the Trifecta Lite best for?"),
    (30, "fishing/phenix-rods-salmon-steelhead.md",   "PASS",    "Does the Cicada come in spinning?"),
    (31, "fishing/phenix-rods-travel.md",             "PASS",    "Does Phenix make travel rods?"),
    (32, "fishing/phenix-rods-travel.md",             "PASS",    "Can I take a Phenix travel rod on a plane?"),
    (33, "fishing/phenix-rods-travel.md",             "PASS",    "What is the lightest Phenix travel rod?"),
    (34, "fishing/phenix-rods-warranty.md",           "PASS",    "My Phenix rod snapped — what do I do?"),
    (35, "fishing/phenix-rods-warranty.md",           "PARTIAL", "My rod broke 2 weeks after I bought it — do I still pay?"),
    (36, "fishing/phenix-rods-warranty.md",           "PASS",    "Do I need to ship my whole rod in for warranty?"),
    (37, "fishing/phenix-rods-warranty.md",           "PASS",    "Can I process warranty through a dealer?"),
    (38, "fishing/phenix-rods-tier-fees.md",          "PASS",    "How much does it cost to replace a Phenix Feather rod?"),
    (39, "fishing/phenix-rods-tier-fees.md",          "PARTIAL", "How much does it cost to replace a Phenix Iron Feather?"),
    (40, "fishing/phenix-rods-tier-fees.md",          "PASS",    "What is the return shipping fee for a 9 foot rod?"),
    (41, "fishing/phenix-rods-tier-fees.md",          "PASS",    "How is the upgrade fee calculated?"),
    (42, "fishing/phenix-rods-pricing.md",            "PARTIAL", "How much does the Cicada cost?"),
    (43, "fishing/phenix-rods-pricing.md",            "PASS",    "What is the most affordable Phenix rod?"),
    (44, "fishing/phenix-rods-pricing.md",            "PASS",    "What is the most premium Phenix rod?"),
    # Hub / cross-series questions — expected source is phenix-rods.md (hub)
    (45, "fishing/phenix-rods-salmon-steelhead.md",   "PARTIAL", "What is the difference between Cicada and Trifecta for salmon?"),
    (46, "fishing/phenix-rods.md",                    "PASS",    "Which Phenix rod is best for beginners?"),
    (47, "fishing/phenix-rods-warranty.md",           "PASS",    "Can I upgrade my rod when I send it in for warranty?"),
    # Semantic / NL tests
    (48, "fishing/phenix-rods-warranty.md",           "PASS",    "My stick snapped — what do I do?"),
    (49, "fishing/phenix-rods-saltwater-inshore.md",  "PARTIAL", "Good tuna rod recommendation?"),
    (50, "fishing/phenix-rods-saltwater-pelagic.md",  "PASS",    "I fish the kelp beds — what rod should I use?"),
]

DOBYNS_PAGES = {
    "fishing/dobyns-rods.md",
    "fishing/dobyns-rods-product-catalog.md",
    "fishing/dobyns-rods-warranty.md",
    "fishing/dobyns-rods-replacement.md",
    "fishing/dobyns-rods-tier-fees.md",
    "fishing/dobyns-rods-care.md",
    "fishing/dobyns-lures.md",
    "fishing/dobyns-reels-combos.md",
}

BUCCA_PAGES = {
    "fishing/bucca-brand.md",
    "fishing/bucca-brand-product-catalog.md",
    "fishing/bucca-brand-warranty.md",
    "fishing/bucca-brand-returns.md",
}

# ── Set up path ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from wiki_retrieval import build_wiki_context, score_wiki_pages, merge_page_keywords, SKIP_PAGES

WIKI_DIR = BASE_DIR / "wiki"

def load_wiki_cache():
    cache = {}
    for page in WIKI_DIR.rglob("*.md"):
        rel = str(page.relative_to(WIKI_DIR)).replace("\\", "/")
        if rel not in SKIP_PAGES:
            cache[rel] = page.read_text()
    return cache


def get_top_pages(query: str, wiki_cache: dict, keywords: dict, n: int = 5) -> list[tuple[str, int]]:
    """Return top-n (page, score) pairs for the query."""
    scores = score_wiki_pages(query, "", wiki_cache, keywords)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(p, s) for p, s in ranked if s > 0][:n]


SEP = "─" * 70

def main():
    print(SEP)
    print("RAG RETRIEVAL REGRESSION TEST — Post Dobyns + Bucca Addition")
    print(SEP)

    wiki_cache = load_wiki_cache()
    keywords   = merge_page_keywords()
    print(f"Wiki pages loaded : {len(wiki_cache)}")
    print(f"Keyword entries   : {len(keywords)}")
    print()

    results = {
        "source_ok":         [],   # expected source retrieved — no regression
        "source_missing":    [],   # expected source NOT in top-5 — regression
        "contaminated":      [],   # Dobyns/Bucca page ranked ABOVE expected source
        "new_contamination": [],   # Dobyns/Bucca page in top-5 (may still be ok if source also there)
    }

    print(f"{'Q#':<4} {'Orig':<8} {'Src OK?':<8} {'Contamination?':<16} {'Top Retrieved Pages'}")
    print(SEP)

    for (qnum, expected_src, orig_result, question) in QUESTIONS:
        top = get_top_pages(question, wiki_cache, keywords, n=6)
        top_pages = [p for p, _ in top]

        # Check if expected source was retrieved
        src_ok = expected_src in top_pages

        # Check for Dobyns/Bucca contamination in top results
        contaminating = [p for p in top_pages if p in DOBYNS_PAGES or p in BUCCA_PAGES]

        # Determine contamination severity
        contaminated_above = False
        if contaminating and src_ok:
            exp_rank = top_pages.index(expected_src)
            cont_ranks = [top_pages.index(p) for p in contaminating]
            contaminated_above = any(r < exp_rank for r in cont_ranks)

        # Record
        entry = {
            "q": qnum,
            "orig": orig_result,
            "question": question[:60],
            "expected": expected_src,
            "top_pages": top_pages[:4],
            "contaminating": contaminating,
        }

        if not src_ok:
            results["source_missing"].append(entry)
        else:
            results["source_ok"].append(entry)

        if contaminated_above:
            results["contaminated"].append(entry)
        elif contaminating:
            results["new_contamination"].append(entry)

        # Status symbols
        src_sym  = "✅" if src_ok else "❌"
        cont_sym = ""
        if contaminated_above:
            cont_sym = "🔴 ABOVE SRC"
        elif contaminating:
            cont_sym = f"🟡 present"

        short_pages = ", ".join(p.split("/")[-1].replace(".md","") for p in top_pages[:4])
        print(f"Q{qnum:<3} {orig_result:<8} {src_sym:<8} {cont_sym:<16} {short_pages}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print(SEP)
    print("SUMMARY")
    print(SEP)
    total = len(QUESTIONS)
    ok    = len(results["source_ok"])
    miss  = len(results["source_missing"])
    cont_above = len(results["contaminated"])
    cont_any   = len(results["new_contamination"])

    print(f"Total questions tested        : {total}")
    print(f"Expected source retrieved     : {ok}/{total}  {'✅ All good' if miss == 0 else f'⚠️  {miss} regressions'}")
    print(f"Cross-brand above source      : {cont_above}  {'✅' if cont_above == 0 else '🔴 REGRESSIONS'}")
    print(f"Cross-brand present (not above): {cont_any}  {'✅' if cont_any == 0 else '🟡 monitor'}")

    if results["source_missing"]:
        print()
        print("❌ SOURCE REGRESSIONS (expected page not retrieved):")
        for e in results["source_missing"]:
            print(f"  Q{e['q']} [{e['orig']}] {e['question']}")
            print(f"       Expected : {e['expected']}")
            print(f"       Got      : {', '.join(p.split('/')[-1] for p in e['top_pages'][:4])}")

    if results["contaminated"]:
        print()
        print("🔴 CROSS-BRAND CONTAMINATION (Dobyns/Bucca ranked ABOVE Phenix source):")
        for e in results["contaminated"]:
            print(f"  Q{e['q']} [{e['orig']}] {e['question']}")
            print(f"       Expected   : {e['expected']}")
            print(f"       Contaminant: {', '.join(e['contaminating'])}")
            print(f"       Top pages  : {', '.join(p.split('/')[-1] for p in e['top_pages'][:4])}")

    if results["new_contamination"]:
        print()
        print("🟡 CROSS-BRAND PRESENT (Dobyns/Bucca in top results but source still retrieved):")
        for e in results["new_contamination"]:
            print(f"  Q{e['q']} [{e['orig']}] {e['question']}")
            print(f"       Contaminant: {', '.join(c.split('/')[-1] for c in e['contaminating'])}")
            print(f"       Top pages  : {', '.join(p.split('/')[-1] for p in e['top_pages'][:4])}")

    print()
    print(SEP)
    print("VERDICT")
    print(SEP)
    if miss == 0 and cont_above == 0:
        if cont_any == 0:
            print("✅ CLEAN — No regressions. All Phenix questions retrieve the correct source.")
            print("   Dobyns and Bucca additions did NOT affect Phenix retrieval quality.")
        else:
            print("🟡 MOSTLY CLEAN — Correct source always retrieved.")
            print(f"   {cont_any} question(s) also pull a Dobyns/Bucca page (non-blocking, monitor).")
    else:
        print(f"🔴 REGRESSIONS DETECTED — {miss} source miss(es), {cont_above} contamination(s).")
        print("   Review the items above before deploying.")


if __name__ == "__main__":
    main()
