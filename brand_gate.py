"""
brand_gate.py  —  GSM Outdoors brand + intent detection gate
─────────────────────────────────────────────────────────────
Changes from previous version:
  1. All brand/keyword config loaded from brand_map.yaml
     — no hardcoded Python dicts
     — add a brand by editing brand_map.yaml only
     — file is auto-reloaded when changed on disk
  2. Fuzzy matching on brand aliases (thefuzz / Levenshtein)
     — handles "Phenox", "Dobins", "Bone Head" etc.
     — only on aliases longer than 5 chars (avoids short-code false positives)
     — threshold: 82 (catches 1-2 char typos, rejects gibberish)
  3. All existing S1-S10 scenario logic preserved
"""

from __future__ import annotations
from pathlib import Path
import yaml

# Fuzzy matching — graceful fallback if not installed
try:
    from thefuzz import fuzz as _fuzz
    _FUZZY_AVAILABLE = True
except ImportError:
    _FUZZY_AVAILABLE = False

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).resolve().parent
BRAND_MAP_PATH = BASE_DIR / "brand_map.yaml"

FUZZY_THRESHOLD = 82   # 82: catches "Phenox"(83), "Dobins"(89), rejects noise
FUZZY_MIN_LEN   = 5    # don't fuzzy-match short codes: sog, wgi, led, app

# ── States ────────────────────────────────────────────────────────────────────
STATE_IDLE               = "idle"
STATE_AWAIT_CATEGORY     = "await_category"
STATE_AWAIT_BRAND        = "await_brand"
STATE_AWAIT_PRODUCT_TYPE = "await_product_type"
STATE_CONFIRMED          = "confirmed"

# ── YAML loader with file-change detection ────────────────────────────────────
_yaml_mtime: float = 0.0
_yaml_cache: dict  = {}

def _load_yaml() -> dict:
    global _yaml_mtime, _yaml_cache
    try:
        mtime = BRAND_MAP_PATH.stat().st_mtime
        if mtime != _yaml_mtime or not _yaml_cache:
            with open(BRAND_MAP_PATH, encoding="utf-8") as f:
                _yaml_cache = yaml.safe_load(f)
            _yaml_mtime = mtime
    except FileNotFoundError:
        raise RuntimeError(
            f"brand_map.yaml not found at {BRAND_MAP_PATH}\n"
            "Copy brand_map.yaml to your project root."
        )
    return _yaml_cache

def get_categories() -> dict:
    return _load_yaml().get("categories", {})

def get_ops_keywords() -> list[str]:
    return _load_yaml().get("ops_keywords", [])

def get_catalogue_keywords() -> list[str]:
    return _load_yaml().get("catalogue_keywords", [])


# ── Helpers ───────────────────────────────────────────────────────────────────
def _n(text: str) -> str:
    return (text or "").lower().strip()

def _scan(text: str, word_list: list[str]) -> list[str]:
    t = _n(text)
    return [w for w in word_list if w in t]

def _build_search_text(query: str, history: list[dict], turns: int = 4) -> str:
    parts = [_n(query)]
    count = 0
    for msg in reversed(history):
        if count >= turns:
            break
        if msg.get("role") in ("user", "assistant"):
            parts.append(_n(msg.get("content", "")))
            count += 1
    return " ".join(parts)


# ── Fuzzy brand matcher ───────────────────────────────────────────────────────
def _fuzzy_match_brand(text: str) -> tuple[str, str] | None:
    """
    Try fuzzy matching text against all brand aliases >= FUZZY_MIN_LEN.
    Returns (brand_name, category) of best match above threshold, or None.

    Strategy:
    - Compare each word in the query against each alias
    - Also compare full query against multi-word aliases (token_set_ratio)
    - Only aliases >= FUZZY_MIN_LEN are tested (avoids short-code false positives)
    """
    if not _FUZZY_AVAILABLE:
        return None

    t = _n(text)
    if len(t) < 3:
        return None

    best_score    = 0
    best_brand    = None
    best_category = None

    for category, data in get_categories().items():
        for brand, aliases in data.get("brand_aliases", {}).items():
            for alias in aliases:
                if len(alias) < FUZZY_MIN_LEN:
                    continue

                # Word-by-word comparison
                for word in t.split():
                    if len(word) < FUZZY_MIN_LEN:
                        continue
                    score = _fuzz.ratio(word, alias)
                    if score > best_score and score >= FUZZY_THRESHOLD:
                        best_score    = score
                        best_brand    = brand
                        best_category = category

                # Full query against multi-word aliases
                if " " in alias:
                    score = _fuzz.token_set_ratio(t, alias)
                    if score > best_score and score >= FUZZY_THRESHOLD:
                        best_score    = score
                        best_brand    = brand
                        best_category = category

    return (best_brand, best_category) if best_brand else None


# ─────────────────────────────────────────────────────────────────────────────
# Core detection
# ─────────────────────────────────────────────────────────────────────────────
def detect_brand(
    query: str,
    history: list[dict],
    session: dict | None = None,
) -> dict:
    session    = session or {}
    q          = _n(query)
    search     = _build_search_text(query, history)
    categories = get_categories()
    ops_kws    = get_ops_keywords()

    # ── S5: ops / catalogue — bypass gate ────────────────────────────
    if _scan(q, ops_kws):
        return _result(False, [], "operations", "exact",
                       STATE_CONFIRMED, "", fuzzy=False)

    # ── Step 1: exact alias scan on current query ─────────────────────
    detected: list[tuple[str, str]] = []
    for cat, data in categories.items():
        for brand, aliases in data.get("brand_aliases", {}).items():
            for alias in aliases:
                if alias in q:
                    detected.append((brand, cat))
                    break

    # Deduplicate
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for b, c in detected:
        if b not in seen:
            seen.add(b)
            unique.append((b, c))

    # ── S9: two brands → comparison ───────────────────────────────────
    if len(unique) >= 2:
        brands = [b for b, _ in unique]
        cats   = list(dict.fromkeys(c for _, c in unique))
        return _result(False, brands,
                       cats[0] if len(cats) == 1 else "mixed",
                       "exact", STATE_CONFIRMED,
                       "Brands: " + ", ".join(brands), fuzzy=False)

    # ── S3/S6: exactly one brand exact match ──────────────────────────
    if len(unique) == 1:
        brand, cat = unique[0]
        return _result(False, [brand], cat, "exact",
                       STATE_CONFIRMED, f"Brand: {brand}", fuzzy=False)

    # ── S4: brand already in session ──────────────────────────────────
    confirmed_brand    = session.get("confirmed_brand")
    confirmed_category = session.get("confirmed_category")
    if confirmed_brand:
        return _result(False, [confirmed_brand], confirmed_category,
                       "exact", STATE_CONFIRMED,
                       f"Brand: {confirmed_brand}", fuzzy=False)

    # ── Step 2: fuzzy match on current query ──────────────────────────
    fm = _fuzzy_match_brand(q)
    if fm:
        brand, cat = fm
        return _result(False, [brand], cat, "fuzzy",
                       STATE_CONFIRMED, f"Brand: {brand}", fuzzy=True)

    # ── Step 3: exact alias scan on history ───────────────────────────
    for cat, data in categories.items():
        for brand, aliases in data.get("brand_aliases", {}).items():
            for alias in aliases:
                if alias in search:
                    return _result(False, [brand], cat, "inferred",
                                   STATE_CONFIRMED,
                                   f"Brand: {brand}", fuzzy=False)

    # Fuzzy on history
    hist_text = _build_search_text("", history, turns=4)
    if hist_text:
        fm2 = _fuzzy_match_brand(hist_text)
        if fm2:
            brand, cat = fm2
            return _result(False, [brand], cat, "fuzzy",
                           STATE_CONFIRMED, f"Brand: {brand}", fuzzy=True)

    # ── Step 4: category detection from keywords ──────────────────────
    detected_cat = None
    for cat, data in categories.items():
        if _scan(q, data.get("keywords", [])):
            detected_cat = cat
            break

    if detected_cat:
        brands   = categories[detected_cat]["brands"]
        question = _ask_brand(detected_cat, brands)
        return _result(True, [], detected_cat, "none",
                       STATE_AWAIT_BRAND, "", question=question, fuzzy=False)

    # ── Step 5: no signal — ask category + product ────────────────────
    return _result(True, [], None, "none",
                   STATE_AWAIT_CATEGORY, "",
                   question=_ask_category_and_product(), fuzzy=False)


# ─────────────────────────────────────────────────────────────────────────────
# Clarification resolvers
# ─────────────────────────────────────────────────────────────────────────────
def resolve_category_answer(answer: str) -> str | None:
    a    = _n(answer)
    cats = list(get_categories().keys())
    for i, cat in enumerate(cats, 1):
        if a.strip() == str(i):
            return cat
    for cat in cats:
        if cat in a:
            return cat
    for cat, data in get_categories().items():
        if _scan(a, data.get("keywords", [])):
            return cat
    return None


def resolve_brand_answer(answer: str, category: str) -> str | None:
    a      = _n(answer)
    data   = get_categories().get(category, {})
    brands = data.get("brands", [])

    for i, brand in enumerate(brands, 1):
        if a.strip() == str(i):
            return brand
    for brand in brands:
        if _n(brand) in a or a in _n(brand):
            return brand
    for brand, aliases in data.get("brand_aliases", {}).items():
        for alias in aliases:
            if alias in a:
                return brand
    # Fuzzy on the answer too
    if _FUZZY_AVAILABLE:
        for brand, aliases in data.get("brand_aliases", {}).items():
            for alias in aliases:
                if len(alias) < FUZZY_MIN_LEN:
                    continue
                for word in a.split():
                    if len(word) < FUZZY_MIN_LEN:
                        continue
                    if _fuzz.ratio(word, alias) >= FUZZY_THRESHOLD:
                        return brand
    return None


def resolve_product_answer(answer: str, category: str) -> str | None:
    a        = _n(answer)
    products = get_categories().get(category, {}).get("products", [])
    for i, prod in enumerate(products, 1):
        if a.strip() == str(i):
            return prod
    for prod in products:
        if any(w in a for w in prod.lower().split() if len(w) > 3):
            return prod
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Question builders
# ─────────────────────────────────────────────────────────────────────────────
def _ask_brand(category: str, brands: list[str]) -> str:
    labels   = {"fishing": "fishing brand",
                "hunting": "hunting brand or product",
                "wireless": "camera brand"}
    label    = labels.get(category, "brand")
    numbered = "\n".join(f"{i+1}. {b}" for i, b in enumerate(brands))
    return (
        f"I can help with that! To make sure I give you the right information, "
        f"which {label} are you contacting us about?\n\n"
        f"{numbered}\n\n"
        f"Just reply with the number or the name."
    )


def _ask_category_and_product() -> str:
    lines = ["I want to make sure I give you the right information.\n",
             "**Which product category is your question about?**\n"]
    for i, (cat, data) in enumerate(get_categories().items(), 1):
        lines.append(f"{i}. **{cat.title()}**")
        for j, prod in enumerate(data.get("products", []), 1):
            lines.append(f"   {chr(96+j)}) {prod}")
    lines.append("\nJust reply with a number (e.g. **1**) or a letter (e.g. **1a**).")
    return "\n".join(lines)


def ask_product_type(category: str) -> str:
    products = get_categories().get(category, {}).get("products", [])
    numbered = "\n".join(f"{i+1}. {p}" for i, p in enumerate(products))
    return (
        f"Got it — **{category.title()}**. "
        f"Which product is your question about?\n\n{numbered}\n\n"
        f"Reply with the number or describe the product."
    )


def ask_brand_after_product(category: str) -> str:
    return _ask_brand(category,
                      get_categories().get(category, {}).get("brands", []))


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_session_defaults() -> dict:
    return {"confirmed_brand": None, "confirmed_category": None,
            "gate_state": STATE_IDLE, "awaiting_clarification": False}


# ─────────────────────────────────────────────────────────────────────────────
# Result builder
# ─────────────────────────────────────────────────────────────────────────────
def _result(
    needs_clarification: bool,
    brands: list[str],
    category: str | None,
    confidence: str,
    state: str,
    hint: str,
    question: str | None = None,
    fuzzy: bool = False,
) -> dict:
    return {
        "needs_clarification": needs_clarification,
        "question":            question,
        "next_state":          state,
        "brands":              brands,
        "category":            category,
        "confidence":          confidence,
        "retrieval_hint":      hint,
        "is_comparison":       len(brands) >= 2,
        "fuzzy_match":         fuzzy,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Module-level aliases — app_v3.py imports these by name
# ─────────────────────────────────────────────────────────────────────────────
BRAND_MAP          = get_categories()
OPS_KEYWORDS       = get_ops_keywords()
CATALOGUE_KEYWORDS = get_catalogue_keywords()
