"""
vector_store.py — Section-level semantic retrieval for GSM Outdoors wiki.

KEY DESIGN CHANGE: retrieval is now SECTION-level, not PAGE-level.

Old approach (broken):
  query → score pages → pick top 4 pages → hope right section is in those pages

New approach (correct):
  query → score every ## section → pick top N sections directly
        → group by page → send best sections to LLM

Why this matters:
  "lightest rod for steelhead on 6 lb line"
  OLD: freshwater-bass page scores 7.6 (noise) → salmon page scores 7.2 (3rd)
       → LLM reads wrong page first, gives wrong answer
  NEW: "Trifecta Lite — lightest steelhead rod" section scores 0.88
       "M1 Bass overview" section scores 0.42 → filtered out
       → LLM reads exactly the right section, correct answer

BUILD:   python vector_store.py --build
TEST:    python vector_store.py --sections "lightest rod for steelhead on 6 lb"
STATUS:  python vector_store.py --status
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
import argparse
import sys
from pathlib import Path
from typing import Optional

import httpx

BASE_DIR  = Path(__file__).resolve().parent
WIKI_DIR  = BASE_DIR / "wiki"
DB_PATH   = WIKI_DIR / ".vectors.db"

OLLAMA_URL  = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"

SKIP_PAGES        = {"index.md", "log.md", "lint-report.md"}
SECTION_MIN_SCORE = 0.62
SECTION_TOP_N     = 10
PAGE_TOP_N        = 4


# ── Embedding + math ──────────────────────────────────────────────────────────

def _embed(text: str) -> list[float]:
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
        )
        resp.raise_for_status()
        return resp.json()["embeddings"][0]


def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


# ── Section chunking ──────────────────────────────────────────────────────────

def _strip_frontmatter(content: str) -> str:
    if not content.strip().startswith("---"):
        return content
    end = content.find("\n---\n", 3)
    return content[end + 5:] if end != -1 else content


def _chunk_page(rel_path: str, content: str) -> list[dict]:
    """Split a wiki page into ## sections. Each chunk is independently embedded."""
    body    = _strip_frontmatter(content)
    pattern = re.compile(r"(?=^## )", re.MULTILINE)
    parts   = pattern.split(body)

    title_m    = re.search(r'^title:\s*"?(.+?)"?\s*$', content[:500], re.MULTILINE)
    page_title = title_m.group(1) if title_m else rel_path

    chunks = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        lines   = part.splitlines()
        heading = lines[0].lstrip("#").strip() if lines else ""
        text    = "\n".join(lines[1:]).strip()
        if len(text) < 30:
            continue
        # embed_text is richer: page title + heading + body for full context
        embed_text = f"{page_title} — {heading}\n\n{text[:1500]}"
        chunks.append({
            "rel_path":        rel_path,
            "section_heading": heading,
            "text":            text,
            "embed_text":      embed_text,
        })

    if not chunks and body.strip():
        chunks.append({
            "rel_path":        rel_path,
            "section_heading": "(overview)",
            "text":            body.strip(),
            "embed_text":      f"{page_title}\n\n{body.strip()[:1500]}",
        })
    return chunks


# ── Database ──────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS sections (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path        TEXT NOT NULL,
            section_heading TEXT NOT NULL,
            text            TEXT NOT NULL,
            embedding       BLOB NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_rel_path ON sections(rel_path)")
    con.commit()
    return con


# ── Build ─────────────────────────────────────────────────────────────────────

def build_vector_store(wiki_dir: Optional[Path] = None) -> int:
    """Embed every ## section of every wiki MD file into SQLite."""
    wiki_dir = wiki_dir or WIKI_DIR
    pages = [p for p in wiki_dir.rglob("*.md") if p.name not in SKIP_PAGES]

    if not pages:
        print("No wiki pages found — run ingest first.")
        return 0

    try:
        _embed("test connection")
    except Exception as e:
        print(f"Cannot reach Ollama: {e}\nRun: ollama serve")
        return 0

    con = _get_db()
    con.execute("DELETE FROM sections")
    con.commit()

    total = 0
    for page in sorted(pages):
        rel     = str(page.relative_to(wiki_dir)).replace("\\", "/")
        content = page.read_text(encoding="utf-8", errors="replace")
        chunks  = _chunk_page(rel, content)

        for chunk in chunks:
            try:
                blob = _vec_to_blob(_embed(chunk["embed_text"]))
                con.execute(
                    "INSERT INTO sections (rel_path, section_heading, text, embedding) "
                    "VALUES (?, ?, ?, ?)",
                    (rel, chunk["section_heading"], chunk["text"], blob),
                )
                total += 1
            except Exception as e:
                print(f"  ⚠ Skipped {rel} § {chunk['section_heading']}: {e}")

        con.commit()
        print(f"  ✓ {rel}  ({len(chunks)} sections)")

    con.close()
    print(f"\nDone — {total} sections embedded into {DB_PATH}")
    return total


# ── SECTION-LEVEL RETRIEVAL ───────────────────────────────────────────────────

def get_top_sections(
    query: str,
    top_n: int = SECTION_TOP_N,
    min_score: float = SECTION_MIN_SCORE,
    category_filter: Optional[str] = None,
    brand_filter: Optional[str] = None,
) -> list[dict]:
    """
    Score every ## section against the query.
    Return the top N sections directly — no page-level aggregation.

    This is the correct way to answer natural language questions.
    "lightest rod for steelhead" → hits the Trifecta Lite section (0.88)
    not the freshwater-bass page (0.76 page-level noise).

    Returns list of { rel_path, section_heading, text, score }
    """
    if not DB_PATH.exists():
        return []

    try:
        q_vec = _embed(query)
    except Exception:
        return []

    con = sqlite3.connect(str(DB_PATH))

    if brand_filter:
        # Narrowest scope — only sections belonging to this brand's pages
        rows = con.execute(
            "SELECT rel_path, section_heading, text, embedding FROM sections "
            "WHERE rel_path LIKE ?",
            (f"{brand_filter}%",)
        ).fetchall()
    elif category_filter:
        # category_filter is e.g. "fishing" — old behaviour kept as fallback
        rows = con.execute(
            "SELECT rel_path, section_heading, text, embedding FROM sections "
            "WHERE rel_path LIKE ?",
            (f"{category_filter}/%",)
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT rel_path, section_heading, text, embedding FROM sections"
        ).fetchall()
    con.close()

    scored = []
    for rel_path, heading, text, blob in rows:
        score = _cosine(q_vec, _blob_to_vec(blob))
        if score >= min_score:
            scored.append({
                "rel_path":        rel_path,
                "section_heading": heading,
                "text":            text,
                "score":           round(score, 4),
            })

    scored.sort(key=lambda x: -x["score"])
    return scored[:top_n]


def build_context_from_sections(
    sections: list[dict],
    max_chars: int = 5000,
    max_pages: int = PAGE_TOP_N,
) -> tuple[str, list[tuple[str, float]]]:
    """
    Build LLM context string from top sections.
    Groups by page, deduplicates, respects char budget.
    Returns (context_string, [(rel_path, best_score), ...])
    """
    if not sections:
        return "", []

    # Group by page — track both bi-encoder score and rerank score (if present)
    page_data: dict[str, dict] = {}
    for sec in sections:
        rel = sec["rel_path"]
        if rel not in page_data:
            page_data[rel] = {"best_score": sec["score"], "best_rank": sec.get("rerank_score", sec["score"]), "sections": []}
        page_data[rel]["sections"].append(sec)
        page_data[rel]["best_score"] = max(page_data[rel]["best_score"], sec["score"])
        page_data[rel]["best_rank"]  = max(page_data[rel]["best_rank"],  sec.get("rerank_score", sec["score"]))

    # When rerank_scores are present (cross-encoder ran), sort pages by best rerank score.
    # Otherwise fall back to bi-encoder best_score so existing behaviour is preserved.
    _has_rerank = any("rerank_score" in s for s in sections)
    _sort_key   = (lambda x: -x[1]["best_rank"]) if _has_rerank else (lambda x: -x[1]["best_score"])
    ranked_pages = sorted(page_data.items(), key=_sort_key)[:max_pages]

    parts = []
    used  = 0
    sources: list[tuple[str, float]] = []

    for rel, data in ranked_pages:
        if used >= max_chars:
            break
        score = data["best_score"]

        header   = f"=== wiki/{rel} (relevance: {score:.2f}) ===\n"
        sec_text = ""
        sort_key = lambda x: x.get("rerank_score", x["score"])  # noqa: E731
        for sec in sorted(data["sections"], key=sort_key, reverse=True):
            chunk = f"\n## {sec['section_heading']}\n{sec['text']}\n"
            if used + len(header) + len(sec_text) + len(chunk) < max_chars:
                sec_text += chunk

        if not sec_text:
            # Budget exhausted — no sections fit; skip header-only entry so
            # budget isn't wasted and downstream pages still get a chance.
            continue

        sources.append((rel, score))
        parts.append(header + sec_text)
        used += len(header) + len(sec_text)

    return "\n\n".join(parts), sources


# ── Page-level helpers (kept for hybrid scoring) ──────────────────────────────

def semantic_search(
    query: str,
    top_k_pages: int = PAGE_TOP_N,
    min_score: float = 0.50,
) -> list[tuple[str, float]]:
    """Deduplicate sections → page-level scores. Used as fallback."""
    sections = get_top_sections(query, top_n=top_k_pages * 3, min_score=min_score)
    seen:  set[str] = set()
    pages: list[tuple[str, float]] = []
    for sec in sections:
        rel = sec["rel_path"]
        if rel not in seen:
            seen.add(rel)
            pages.append((rel, sec["score"]))
        if len(pages) >= top_k_pages:
            break
    return pages


def hybrid_score_pages(
    query: str,
    history_text: str,
    wiki_cache: dict,
    keyword_scores: dict[str, int],
    brand_hint: str = "",
) -> dict[str, float]:
    """Combine semantic + keyword for page-level ranking (legacy mode)."""
    semantic = dict(semantic_search(query, top_k_pages=8, min_score=0.50))
    combined: dict[str, float] = {}
    for rel in set(keyword_scores) | set(semantic):
        if rel not in wiki_cache:
            continue
        combined[rel] = float(keyword_scores.get(rel, 0)) + semantic.get(rel, 0.0) * 10.0
    return combined


# ── Variant embeddings ────────────────────────────────────────────────────────
# Embeds question_variants from intent_map.json into a separate DB table.
# Each variant is customer-language text → cosine match against customer queries
# is tighter than matching against wiki prose.

INTENT_MAP_PATH = WIKI_DIR / "intent_map.json"


def build_variant_embeddings(intent_map_path: Optional[Path] = None) -> int:
    """
    Embed all question_variants from intent_map.json into variant_sections table.
    Incremental — skips already-embedded variants. Safe to re-run.
    Prerequisite: python build_intent_map.py must have run first.
    """
    intent_map_path = intent_map_path or INTENT_MAP_PATH
    if not intent_map_path.exists():
        print("intent_map.json not found — run: python build_intent_map.py")
        return 0

    try:
        entries = json.loads(intent_map_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Failed to load intent_map.json: {e}")
        return 0

    try:
        _embed("test")
    except Exception as e:
        print(f"Cannot reach Ollama: {e}\nRun: ollama serve")
        return 0

    con = _get_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS variant_sections (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path        TEXT NOT NULL,
            section_heading TEXT NOT NULL,
            variant_text    TEXT NOT NULL,
            embedding       BLOB NOT NULL,
            UNIQUE(rel_path, section_heading, variant_text)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_variant_rel ON variant_sections(rel_path)"
    )
    con.commit()

    existing = {
        (r[0], r[1], r[2])
        for r in con.execute(
            "SELECT rel_path, section_heading, variant_text FROM variant_sections"
        )
    }

    total = 0
    skipped = 0
    for entry in entries:
        rel     = entry.get("rel_path", "")
        heading = entry.get("section_heading", "")
        variants = [
            v for v in entry.get("question_variants", [])
            if isinstance(v, str) and v.strip()
        ]
        for variant in variants:
            key = (rel, heading, variant)
            if key in existing:
                skipped += 1
                continue
            try:
                blob = _vec_to_blob(_embed(variant))
                con.execute(
                    "INSERT OR IGNORE INTO variant_sections "
                    "(rel_path, section_heading, variant_text, embedding) "
                    "VALUES (?, ?, ?, ?)",
                    (rel, heading, variant, blob),
                )
                existing.add(key)
                total += 1
            except Exception as e:
                print(f"  [WARN] embed failed for variant '{variant[:40]}': {e}")

        if total > 0 and total % 200 == 0:
            con.commit()
            print(f"  {total} variants embedded...")

    con.commit()
    con.close()
    print(f"Done — {total} new variant embeddings, {skipped} skipped")
    return total


def get_top_sections_with_variants(
    query: str,
    top_n: int = SECTION_TOP_N,
    min_score: float = SECTION_MIN_SCORE,
    category_filter: Optional[str] = None,
    brand_filter: Optional[str] = None,
    # variant_boost: float = 1.05,  # v1 — caused wrong pages to outscore warranty page (0.93+)
    variant_boost: float = 0.85,   # v2 — demotes variant matches; tie-breaker only
) -> list[dict]:
    """
    Hybrid section retrieval combining section-text and question-variant embeddings.

    Section embeddings:  query vs wiki prose   — good for technical / model-number queries
    Variant embeddings:  query vs customer questions — good for informal / emotional queries

    Merges by (rel_path, section_heading), keeping the higher score.
    Variant matches receive a small boost (default 5%) to favour customer-language alignment
    when scores are close.

    Returns same format as get_top_sections: [{rel_path, section_heading, text, score}]
    """
    if not DB_PATH.exists():
        return []

    try:
        q_vec = _embed(query)
    except Exception:
        return []

    con = sqlite3.connect(str(DB_PATH))

    # ── Build shared filter ───────────────────────────────────────────────────
    if brand_filter:
        filter_sql    = "WHERE rel_path LIKE ?"
        filter_params: tuple = (f"{brand_filter}%",)
    elif category_filter:
        filter_sql    = "WHERE rel_path LIKE ?"
        filter_params = (f"{category_filter}/%",)
    else:
        filter_sql    = ""
        filter_params = ()

    # ── Score section embeddings (existing table) ─────────────────────────────
    section_rows = con.execute(
        f"SELECT rel_path, section_heading, text, embedding FROM sections {filter_sql}",
        filter_params,
    ).fetchall()

    scores: dict[tuple, dict] = {}
    for rel, heading, text, blob in section_rows:
        score = _cosine(q_vec, _blob_to_vec(blob))
        if score >= min_score:
            key = (rel, heading)
            scores[key] = {
                "rel_path":        rel,
                "section_heading": heading,
                "text":            text,
                "score":           round(score, 4),
            }

    # ── Score variant embeddings (new table, if built) ────────────────────────
    has_variants = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='variant_sections'"
    ).fetchone()

    if has_variants:
        var_rows = con.execute(
            f"SELECT rel_path, section_heading, variant_text, embedding "
            f"FROM variant_sections {filter_sql}",
            filter_params,
        ).fetchall()

        for rel, heading, variant_text, blob in var_rows:
            score = _cosine(q_vec, _blob_to_vec(blob))
            if score < min_score:
                continue
            boosted = round(min(1.0, score * variant_boost), 4)
            key = (rel, heading)
            if key in scores:
                if boosted > scores[key]["score"]:
                    scores[key]["score"] = boosted
            else:
                # Variant matched but section was below min_score — look up text
                row = con.execute(
                    "SELECT text FROM sections WHERE rel_path=? AND section_heading=?",
                    (rel, heading),
                ).fetchone()
                scores[key] = {
                    "rel_path":        rel,
                    "section_heading": heading,
                    "text":            row[0] if row else "",
                    "score":           boosted,
                }

    con.close()

    result = sorted(scores.values(), key=lambda x: -x["score"])
    return result[:top_n]


# ── Cross-encoder reranker ────────────────────────────────────────────────────
# Lazy-loaded on first call to rerank_sections().  Falls back silently if
# sentence-transformers is not installed.  One-time ~9 s cold-start on the
# first query after app launch (model is cached on disk after that).

_RERANKER: Optional[object] = None
_RERANKER_LOADED: bool = False
_RERANKER_MODEL  = "cross-encoder/ms-marco-MiniLM-L-6-v2"


def _load_reranker() -> bool:
    global _RERANKER, _RERANKER_LOADED
    if _RERANKER_LOADED:
        return _RERANKER is not None
    print("[reranker] Loading cross-encoder model...", flush=True)
    try:
        from sentence_transformers import CrossEncoder  # type: ignore
        _RERANKER = CrossEncoder(_RERANKER_MODEL)
        _RERANKER_LOADED = True
        print("[reranker] Ready ✓", flush=True)
        return True
    except Exception as e:
        _RERANKER_LOADED = True  # don't retry
        print(f"[reranker] FAILED to load: {e}", flush=True)
        return False


def rerank_sections(query: str, sections: list[dict]) -> list[dict]:
    """
    Re-score sections with cross-encoder and return them highest-first.

    Uses the ORIGINAL (unexpanded) query so the cross-encoder sees natural
    language rather than keyword-stuffed expansions designed for bi-encoders.
    Falls back to original order when sentence-transformers is unavailable.
    """
    if not sections:
        return sections
    if not _load_reranker() or _RERANKER is None:
        return sections
    pairs = [(query, f"{s['section_heading']}\n{s['text']}") for s in sections]
    raw_scores = _RERANKER.predict(pairs)  # logit scores — higher = more relevant
    reranked = sorted(
        zip(raw_scores, sections),
        key=lambda x: float(x[0]),
        reverse=True,
    )
    result = []
    for rerank_score, sec in reranked:
        sec = dict(sec)  # shallow copy — don't mutate the original
        sec["rerank_score"] = round(float(rerank_score), 4)
        result.append(sec)
    return result


def reranker_ready() -> bool:
    return _load_reranker()


# ── Ready check ───────────────────────────────────────────────────────────────

def is_ready() -> bool:
    if not DB_PATH.exists():
        return False
    try:
        con   = sqlite3.connect(str(DB_PATH))
        count = con.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
        con.close()
        return count > 0
    except Exception:
        return False




# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GSM Outdoors — Vector Store")
    parser.add_argument("--build",          action="store_true", help="Build/rebuild vector store")
    parser.add_argument("--build-variants", action="store_true", help="Embed question_variants from intent_map.json")
    parser.add_argument("--query",          type=str,            help="Page-level search test")
    parser.add_argument("--sections",       type=str,            help="Section-level search test")
    parser.add_argument("--status",         action="store_true", help="Show status")
    args = parser.parse_args()

    if args.build:
        sys.exit(0 if build_vector_store() > 0 else 1)

    elif args.build_variants:
        sys.exit(0 if build_variant_embeddings() > 0 else 1)

    elif args.sections:
        if not is_ready():
            print("Not built. Run: python vector_store.py --build")
            sys.exit(1)
        print(f"\nSection search: {args.sections}")
        print("-" * 70)
        for sec in get_top_sections(args.sections, top_n=8, min_score=0.55):
            print(f"  {sec['score']:.4f}  {sec['rel_path']} § {sec['section_heading']}")

    elif args.query:
        if not is_ready():
            print("Not built. Run: python vector_store.py --build")
            sys.exit(1)
        print(f"\nPage search: {args.query}")
        print("-" * 70)
        for rel, score in semantic_search(args.query, top_k_pages=5, min_score=0.45):
            print(f"  {score:.4f}  {rel}")

    elif args.status:
        if is_ready():
            con      = sqlite3.connect(str(DB_PATH))
            count    = con.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
            pages    = con.execute("SELECT COUNT(DISTINCT rel_path) FROM sections").fetchone()[0]
            size     = DB_PATH.stat().st_size // 1024
            has_var  = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='variant_sections'"
            ).fetchone()
            var_count = con.execute("SELECT COUNT(*) FROM variant_sections").fetchone()[0] if has_var else 0
            con.close()
            print(f"✅ Ready — {pages} pages, {count} sections, {size} KB")
            if var_count:
                print(f"   Variants — {var_count} question-variant embeddings")
            else:
                print("   Variants — not built. Run: python vector_store.py --build-variants")
        else:
            print("❌ Not built. Run: python vector_store.py --build")
    else:
        parser.print_help()
