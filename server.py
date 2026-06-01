#!/usr/bin/env python3
"""
server.py — Wiki Studio Backend
────────────────────────────────
FastAPI server that powers the Wiki Studio UI.
Handles file uploads, real ingest, wiki CRUD, and live log streaming.

Usage:
    python server.py
    # then open http://localhost:8080

Runs alongside Chainlit chat on port 8000:
    chainlit run app.py --port 8000
"""

from __future__ import annotations

import os
import sys
import json
import asyncio
import shutil
import subprocess
import tempfile
import difflib
import time
from pathlib import Path
from datetime import datetime
from typing import AsyncGenerator, List, Optional

import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from wiki_retrieval import (
    append_keyword_supplements,
    build_wiki_context,
    extract_frontmatter_lists,
    score_wiki_pages,
    select_ranked_pages,
)
from ingest_customer import (
    OLLAMA_URL,
    LLM_MODEL,
    classify_area_to_wiki,
    default_stub_page,
    describe_image,
    generate_wiki_draft,
    parse_qa_excel,
)

# ── Paths ─────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
RAW_DIR    = BASE_DIR / "raw"
WIKI_DIR   = BASE_DIR / "wiki"
INGEST_PY  = BASE_DIR / "ingest_customer.py"
INDEX_PATH = WIKI_DIR / "index.md"
LOG_PATH   = WIKI_DIR / "log.md"

RAW_DIR.mkdir(exist_ok=True)
WIKI_DIR.mkdir(exist_ok=True)
for d in ("fishing", "hunting", "wireless"):
    (WIKI_DIR / d).mkdir(exist_ok=True)

# ── App ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Wiki Studio", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global ingest state (simple in-memory for single-user) ────────────────
ingest_state = {
    "running": False,
    "progress": 0,
    "log": [],
    "current_file": "",
}

# ── Folder → wiki page mapping (mirrors ingest_customer.py) ──────────────
FOLDER_WIKI_MAP = [
    ("Rods and Baits - Bonehead Tackle",  "fishing/bonehead-tackle.md"),
    ("Rods and Baits - Dobyns",           "fishing/dobyns-rods.md"),
    ("Rods - Phenix",                     "fishing/phenix-rods.md"),
    ("FISHING ROD WARRANTY FORMS",        "fishing/phenix-rods.md"),
    ("Baits - Bucca Brand",               "fishing/bucca-brand.md"),
    ("Fishing Dealer Inquiry",            "fishing/dealer-inquiry.md"),
    ("Fishing Training",                  "fishing/fishing-overview.md"),
    ("CS FISHING",                        "fishing/fishing-overview.md"),
    ("Avian-X Parts",                     "hunting/avian-x.md"),
    ("Feeder & Timer Manuals",            "hunting/feeders-and-timers.md"),
    ("Feeders & Timers",                  "hunting/feeders-and-timers.md"),
    ("Boss Buck",                         "hunting/feeders-and-timers.md"),
    ("SOG",                               "hunting/sog-knives.md"),
    ("Comparison Docs",                   "hunting/product-comparisons.md"),
    ("Procedures",                        "hunting/procedures.md"),
    ("Replacement Parts File",            "hunting/replacement-parts.md"),
    ("CS HUNTING",                        "hunting/replacement-parts.md"),
    ("Connect Cellular",                  "wireless/connect-cellular.md"),
    ("MUD-MTRX",                          "wireless/muddy-mtrx.md"),
    ("STC-DS4KTM",                        "wireless/stealth-cam.md"),
    ("Camera Manuals",                    "wireless/wireless-overview.md"),
    ("GT - Wireless Tech Support",        "wireless/wireless-overview.md"),
    ("App Support",                       "agent-operations.md"),
]

ALL_TARGETS = [
    "agent-operations.md",
    "fishing/fishing-overview.md", "fishing/phenix-rods.md",
    "fishing/dobyns-rods.md", "fishing/bucca-brand.md",
    "fishing/bonehead-tackle.md", "fishing/dealer-inquiry.md",
    "hunting/feeders-and-timers.md", "hunting/avian-x.md",
    "hunting/replacement-parts.md", "hunting/sog-knives.md",
    "hunting/product-comparisons.md", "hunting/procedures.md",
    "hunting/box-blinds.md", "hunting/walkers.md",
    "wireless/wireless-overview.md", "wireless/connect-cellular.md",
    "wireless/muddy-mtrx.md", "wireless/stealth-cam.md",
]

def guess_target(filename: str, folder: str = "") -> str:
    """Auto-map a file to its target wiki page."""
    search = (folder + " " + filename).replace("/", " ").replace("\\", " ")
    for fragment, target in FOLDER_WIKI_MAP:
        if fragment.lower() in search.lower():
            return target
    return ALL_TARGETS[0]


def add_log(msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    ingest_state["log"].append({"ts": ts, "level": level, "msg": msg})
    if len(ingest_state["log"]) > 200:
        ingest_state["log"] = ingest_state["log"][-200:]


# ── API: Filesystem ───────────────────────────────────────────────────────

@app.get("/api/tree")
def get_tree():
    """Return raw/ and wiki/ folder trees."""
    def scan_dir(path: Path, rel: Path = None) -> dict:
        if rel is None:
            rel = path
        result = {"name": path.name, "path": str(path.relative_to(BASE_DIR)), "children": [], "files": []}
        try:
            for item in sorted(path.iterdir()):
                if item.name.startswith(".") or item.name.startswith("~"):
                    continue
                if item.is_dir():
                    result["children"].append(scan_dir(item, rel))
                else:
                    result["files"].append({
                        "name": item.name,
                        "path": str(item.relative_to(BASE_DIR)),
                        "size": item.stat().st_size,
                        "ext": item.suffix.lower().lstrip("."),
                        "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat(),
                    })
        except PermissionError:
            pass
        return result

    return {
        "raw": scan_dir(RAW_DIR),
        "wiki": scan_dir(WIKI_DIR),
    }


@app.get("/api/raw/folders")
def get_raw_folders():
    """List all subfolders inside raw/ for the folder selector."""
    folders = ["(root)"]
    for p in sorted(RAW_DIR.rglob("*")):
        if p.is_dir():
            folders.append(str(p.relative_to(RAW_DIR)))
    return {"folders": folders}


# ── API: File Upload ──────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    folder: str = Form(""),
    target: str = Form(""),
):
    """Upload a document to raw/ (optionally into a subfolder)."""
    # Resolve destination folder
    if folder and folder != "(root)":
        dest_dir = RAW_DIR / folder
    else:
        dest_dir = RAW_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / file.filename
    content = await file.read()
    dest_path.write_bytes(content)

    # Auto-detect target if not specified
    if not target:
        target = guess_target(file.filename, folder)

    return {
        "name": file.filename,
        "path": str(dest_path.relative_to(BASE_DIR)),
        "size": len(content),
        "target": target,
        "folder": folder or "(root)",
    }


@app.post("/api/raw/folder")
def create_raw_folder(data: dict):
    """Create a new folder inside raw/."""
    name = data.get("name", "").strip().strip("/")
    if not name:
        raise HTTPException(400, "Folder name required")
    path = RAW_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    return {"path": str(path.relative_to(BASE_DIR)), "name": name}


@app.delete("/api/raw/file")
def delete_raw_file(data: dict):
    """Delete a file from raw/."""
    rel = data.get("path", "").lstrip("/")
    path = BASE_DIR / rel
    if path.exists() and path.is_relative_to(RAW_DIR):
        path.unlink()
        return {"deleted": rel}
    raise HTTPException(404, "File not found")


# ── API: Wiki CRUD ────────────────────────────────────────────────────────

@app.get("/api/wiki")
def list_wiki_pages():
    """Return all wiki .md pages with metadata."""
    pages = []
    skip = {"log.md", "lint-report.md"}
    for p in sorted(WIKI_DIR.rglob("*.md")):
        if p.name in skip:
            continue
        rel = str(p.relative_to(WIKI_DIR))
        content = p.read_text(errors="replace")
        # Parse frontmatter title
        title = rel
        for line in content.splitlines()[:12]:
            if line.startswith("title:"):
                title = line.replace("title:", "").strip().strip('"\'')
                break
        category = "ops"
        if rel.startswith("fishing"):  category = "fishing"
        elif rel.startswith("hunting"): category = "hunting"
        elif rel.startswith("wireless"): category = "wireless"
        pages.append({
            "path": rel,
            "name": p.name,
            "title": title,
            "category": category,
            "size": p.stat().st_size,
            "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
            "has_frontmatter": content.strip().startswith("---"),
        })
    return {"pages": pages}


@app.get("/api/wiki/{path:path}")
def get_wiki_page(path: str):
    """Read a wiki page content."""
    page_path = WIKI_DIR / path
    if not page_path.exists():
        raise HTTPException(404, f"Page not found: {path}")
    return {"path": path, "content": page_path.read_text(errors="replace")}


@app.put("/api/wiki/{path:path}")
def save_wiki_page(path: str, data: dict):
    """Write a wiki page to disk."""
    page_path = WIKI_DIR / path
    page_path.parent.mkdir(parents=True, exist_ok=True)
    content = data.get("content", "")
    page_path.write_text(content)
    # Append to log
    _append_log(f"wiki-edit | {path}")
    return {
        "path": path,
        "size": len(content),
        "saved": True,
        "modified": datetime.now().isoformat(),
    }


@app.delete("/api/wiki/{path:path}")
def delete_wiki_page(path: str):
    """Delete a wiki page."""
    page_path = WIKI_DIR / path
    if not page_path.exists():
        raise HTTPException(404, "Page not found")
    page_path.unlink()
    # Clean up empty directories
    try:
        page_path.parent.rmdir()
    except OSError:
        pass
    return {"deleted": path}


@app.post("/api/wiki/folder")
def create_wiki_folder(data: dict):
    """Create a new folder inside wiki/."""
    name = data.get("name", "").strip().strip("/")
    if not name:
        raise HTTPException(400, "Folder name required")
    path = WIKI_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    return {"path": name, "name": name}


# ── API: Ingest ───────────────────────────────────────────────────────────

@app.post("/api/ingest/start")
def start_ingest(data: dict):
    """
    Start ingest as a background subprocess.
    files: list of {path: "raw/...", target: "wiki/..."}
    force: bool
    """
    if ingest_state["running"]:
        return {"status": "already_running"}

    files = data.get("files", [])
    force = data.get("force", False)
    mode = data.get("mode", "all")  # "all", "selected", "folder"

    ingest_state["running"] = True
    ingest_state["progress"] = 0
    ingest_state["log"] = []
    ingest_state["current_file"] = ""

    add_log(f"Wiki Studio — Ingest started", "info")
    add_log(f"Mode: {mode} | Files: {len(files)} | Force: {force}", "info")

    # Run ingest in a background thread
    import threading

    def run():
        try:
            if not INGEST_PY.exists():
                add_log("ingest_customer.py not found — running demo mode", "warn")
                _demo_ingest(files)
                return

            cmd = [sys.executable, str(INGEST_PY)]

            if mode == "all":
                cmd.append("--all")
            elif mode == "selected" and files:
                # Ingest each file individually
                total = len(files)
                for i, f in enumerate(files):
                    fpath = BASE_DIR / f["path"]
                    if not fpath.exists():
                        add_log(f"File not found: {f['path']}", "warn")
                        continue
                    add_log(f"Ingesting: {fpath.name}", "info")
                    ingest_state["current_file"] = fpath.name
                    sub = subprocess.run(
                        [sys.executable, str(INGEST_PY), "--file", str(fpath)]
                        + (["--force"] if force else []),
                        capture_output=True, text=True, cwd=str(BASE_DIR),
                    )
                    for line in (sub.stdout + sub.stderr).splitlines():
                        if line.strip():
                            level = "ok" if "Wrote" in line else "err" if "ERROR" in line else "info"
                            add_log(line.strip(), level)
                    ingest_state["progress"] = int((i + 1) / total * 90)
                ingest_state["progress"] = 95
                add_log("Rebuilding index.md…", "info")
                subprocess.run(
                    [sys.executable, str(INGEST_PY), "--reindex"],
                    capture_output=True, text=True, cwd=str(BASE_DIR),
                )
                add_log("✓ index.md rebuilt", "ok")
            else:
                cmd.extend(["--folder", str(RAW_DIR)])

            if mode == "all":
                if force:
                    cmd.append("--force")
                add_log(f"Running: {' '.join(cmd[2:])}", "info")
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, cwd=str(BASE_DIR),
                )
                lines_seen = 0
                for line in proc.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    lines_seen += 1
                    level = "ok" if "Wrote" in line or "✓" in line else \
                            "err" if "ERROR" in line or "✗" in line else \
                            "warn" if "WARNING" in line or "empty" in line.lower() else "info"
                    add_log(line, level)
                    # Estimate progress from log density
                    ingest_state["progress"] = min(90, lines_seen * 2)
                proc.wait()

            ingest_state["progress"] = 100
            add_log("✓ Ingest complete", "ok")
            add_log(f"Wiki pages: {len(list(WIKI_DIR.rglob('*.md')))}", "ok")

        except Exception as e:
            add_log(f"Ingest error: {e}", "err")
        finally:
            ingest_state["running"] = False
            ingest_state["current_file"] = ""

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started"}


def _demo_ingest(files):
    """Simulate ingest when ingest_customer.py is not present."""
    import time, random
    total = max(len(files), 3)
    demo_files = files if files else [
        {"path": "raw/sample.pdf", "target": "fishing/phenix-rods.md"},
        {"path": "raw/sample2.docx", "target": "hunting/feeders-and-timers.md"},
        {"path": "raw/sample3.xlsx", "target": "wireless/connect-cellular.md"},
    ]
    for i, f in enumerate(demo_files):
        name = Path(f["path"]).name
        add_log(f"Ingesting: {name}", "info")
        ingest_state["current_file"] = name
        time.sleep(0.8 + random.random() * 0.4)
        if random.random() > 0.1:
            target = f.get("target", ALL_TARGETS[0])
            add_log(f"  → Wrote wiki/{target} ({random.randint(800, 2500)} chars)", "ok")
            # Write a demo wiki page
            page_path = WIKI_DIR / target
            page_path.parent.mkdir(parents=True, exist_ok=True)
            if not page_path.exists():
                page_path.write_text(f"---\ntitle: \"{Path(target).stem.replace('-',' ').title()}\"\ncategory: demo\nlast_updated: {datetime.now().date()}\n---\n\n# Demo Page\n\nThis page was created by the demo ingest.\n")
        else:
            add_log(f"  ✗ LLM returned empty — retry with --force", "err")
        ingest_state["progress"] = int((i + 1) / total * 90)
    time.sleep(0.3)
    ingest_state["progress"] = 95
    add_log("Rebuilding index.md…", "info")
    time.sleep(0.5)
    add_log("✓ index.md rebuilt", "ok")
    ingest_state["progress"] = 100
    ingest_state["running"] = False


@app.get("/api/ingest/status")
def ingest_status():
    """Poll ingest progress."""
    return {
        "running": ingest_state["running"],
        "progress": ingest_state["progress"],
        "current_file": ingest_state["current_file"],
        "log": ingest_state["log"][-50:],
    }


@app.get("/api/ingest/stream")
async def ingest_stream():
    """Server-Sent Events stream for live ingest log."""
    async def event_generator() -> AsyncGenerator[str, None]:
        last_idx = 0
        while True:
            log = ingest_state["log"]
            if len(log) > last_idx:
                for entry in log[last_idx:]:
                    data = json.dumps(entry)
                    yield f"data: {data}\n\n"
                last_idx = len(log)
            if not ingest_state["running"] and last_idx >= len(ingest_state["log"]):
                yield f"data: {json.dumps({'done': True, 'progress': ingest_state['progress']})}\n\n"
                break
            await asyncio.sleep(0.15)
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/ingest/stop")
def stop_ingest():
    ingest_state["running"] = False
    add_log("Ingest stopped by user", "warn")
    return {"stopped": True}


# ── API: Index ────────────────────────────────────────────────────────────

@app.post("/api/index/rebuild")
def rebuild_index():
    """Rebuild index.md using ingest_customer.py --reindex."""
    if not INGEST_PY.exists():
        # Generate a simple index manually
        pages = list(WIKI_DIR.rglob("*.md"))
        lines = [
            "---", f'title: "GSM Outdoors CS Wiki — Index"',
            f"last_updated: {datetime.now().date()}",
            f"total_pages: {len(pages)}", "---", "",
            "# GSM Outdoors CS Wiki — Index", "",
        ]
        for section, prefix in [("Operations", ""), ("Fishing", "fishing/"),
                                  ("Hunting", "hunting/"), ("Wireless", "wireless/")]:
            lines.append(f"## {section}")
            lines.append("| Page | What customers find here |")
            lines.append("|---|---|")
            for p in sorted(pages):
                rel = str(p.relative_to(WIKI_DIR))
                if (prefix == "" and "/" not in rel and rel not in ("index.md","log.md")) or \
                   (prefix and rel.startswith(prefix)):
                    name = p.stem.replace("-", " ").title()
                    lines.append(f"| [[{rel.replace('.md','')}]] | {name} |")
            lines.append("")
        INDEX_PATH.write_text("\n".join(lines))
        return {"rebuilt": True, "pages": len(pages)}

    result = subprocess.run(
        [sys.executable, str(INGEST_PY), "--reindex"],
        capture_output=True, text=True, cwd=str(BASE_DIR),
    )
    return {
        "rebuilt": True,
        "output": result.stdout[-500:] if result.stdout else "",
        "pages": len(list(WIKI_DIR.rglob("*.md"))),
    }


@app.post("/api/lint")
def run_lint():
    """Run wiki lint check."""
    issues = []
    pages = list(WIKI_DIR.rglob("*.md"))
    for p in pages:
        rel = str(p.relative_to(WIKI_DIR))
        content = p.read_text(errors="replace")
        if not content.strip().startswith("---"):
            issues.append({"type": "error", "page": rel, "msg": "Missing YAML frontmatter"})
        agent_phrases = ["tell the customer","advise the customer","inform the customer",
                         "direct the customer","the agent should"]
        lower = content.lower()
        for phrase in agent_phrases:
            if phrase in lower:
                issues.append({"type": "warn", "page": rel, "msg": f"Agent language found: '{phrase}'"})
                break
        lines = content.splitlines()
        half = len(lines) // 2
        if half > 20:
            if "\n".join(lines[:half]).strip()[:200] == "\n".join(lines[half:]).strip()[:200]:
                issues.append({"type": "warn", "page": rel, "msg": "Duplicate content detected"})
    return {"issues": issues, "total_pages": len(pages), "clean": len(issues) == 0}


# ── API: Stats ────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    wiki_pages = list(WIKI_DIR.rglob("*.md"))
    raw_files  = [f for f in RAW_DIR.rglob("*") if f.is_file()
                  and not f.name.startswith(".") and not f.name.startswith("~")]
    has_fm = sum(1 for p in wiki_pages if p.read_text(errors="replace").strip().startswith("---"))
    return {
        "wiki_pages":   len(wiki_pages),
        "with_frontmatter": has_fm,
        "raw_files":    len(raw_files),
        "index_exists": INDEX_PATH.exists(),
        "last_log":     _last_log_entry(),
        "ingest_running": ingest_state["running"],
    }


def _last_log_entry() -> str:
    if not LOG_PATH.exists():
        return "Never"
    lines = LOG_PATH.read_text().splitlines()
    for line in reversed(lines):
        if line.startswith("## ["):
            return line[4:14]
    return "Never"


def _append_log(entry: str):
    today = datetime.now().date().isoformat()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text("# GSM Outdoors CS Wiki — Log\n\n")
    with open(LOG_PATH, "a") as f:
        f.write(f"\n## [{today}] {entry}\n")


# ── Auto-ingest (Excel tickets) + retrieval test ───────────────────────────

TEST_RESULTS_PATH = BASE_DIR / "test_results.json"

TEST_QUERY_SYSTEM = """You are a GSM Outdoors customer support assistant.
Answer ONLY using the wiki excerpts in the user message. Be concise.
If the information is not in the excerpts, say you do not have that detail."""


def _safe_wiki_rel(path: str) -> str:
    p = (path or "").replace("\\", "/").strip().lstrip("/")
    if not p or any(seg == ".." for seg in p.split("/")):
        raise HTTPException(400, "Invalid wiki path")
    return p


def _wiki_disk_cache() -> dict[str, str]:
    cache: dict[str, str] = {}
    for p in WIKI_DIR.rglob("*.md"):
        rel = str(p.relative_to(WIKI_DIR)).replace("\\", "/")
        cache[rel] = p.read_text(errors="replace")
    return cache


@app.post("/api/ingest/upload")
async def api_ingest_upload(files: List[UploadFile] = File(...)):
    """Excel → Q&A drafts with unified diff. Images → VLM description."""
    if not files:
        raise HTTPException(400, "No files uploaded")
    drafts: list[dict] = []
    image_notes: list[dict] = []
    tmp = Path(tempfile.mkdtemp(prefix="ingest_upload_"))
    try:
        for uf in files:
            raw = await uf.read()
            if not raw:
                continue
            name = uf.filename or "upload"
            path = tmp / Path(name).name
            path.write_bytes(raw)
            ext = path.suffix.lower()
            if ext in (".xlsx", ".xls"):
                try:
                    by_area = parse_qa_excel(path)
                except Exception as e:
                    raise HTTPException(400, f"Excel parse failed ({name}): {e}") from e
                by_target: dict[str, list[dict]] = {}
                for area, qa_list in by_area.items():
                    for qa in qa_list:
                        subj = str(qa.get("subject", "") or "")
                        blob = f"{qa.get('q', '')} {qa.get('a', '')}"
                        target = classify_area_to_wiki(area, subj, blob)
                        by_target.setdefault(target, []).append(qa)
                for target_file, pairs in by_target.items():
                    rel = target_file.replace("\\", "/")
                    wiki_path = WIKI_DIR / rel
                    exists = wiki_path.is_file()
                    existing_content = wiki_path.read_text(encoding="utf-8") if exists else ""
                    if not exists:
                        existing_content = default_stub_page(rel)
                    new_section = generate_wiki_draft(rel, pairs, existing_content)
                    merged_preview = existing_content.rstrip() + "\n\n" + new_section.rstrip() + "\n"
                    diff_text = "".join(
                        difflib.unified_diff(
                            existing_content.splitlines(True),
                            merged_preview.splitlines(True),
                            fromfile="a/" + rel,
                            tofile="b/" + rel,
                        )
                    )
                    drafts.append(
                        {
                            "target_file": rel,
                            "status": "exists" if exists else "new",
                            "existing_content": existing_content,
                            "new_section": new_section,
                            "merged_preview": merged_preview,
                            "diff": diff_text,
                        }
                    )
            elif ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                desc = describe_image(path)
                image_notes.append({"file": name, "description": desc or "(no description — check Ollama / model)"})
            else:
                image_notes.append({"file": name, "skipped": True, "reason": f"Auto-ingest batch only processes Excel or images; got {ext or 'unknown'}"})
        return {"drafts": drafts, "images": image_notes}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class IngestApproveBody(BaseModel):
    target_file: str
    final_content: str


@app.post("/api/ingest/approve")
def api_ingest_approve(body: IngestApproveBody):
    rel = _safe_wiki_rel(body.target_file)
    page = WIKI_DIR / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(body.final_content, encoding="utf-8")
    brands = extract_frontmatter_lists(body.final_content, "brands")
    topics = extract_frontmatter_lists(body.final_content, "topics")
    append_keyword_supplements(rel, brands, topics)
    if INGEST_PY.exists():
        subprocess.run(
            [sys.executable, str(INGEST_PY), "--reindex"],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
        )
    _append_log(f"auto-ingest-approve | {rel}")
    return {"saved": True, "file": rel}


class TestQueryBody(BaseModel):
    question: str


@app.post("/api/test/query")
def api_test_query(body: TestQueryBody):
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(400, "question required")
    t0 = time.time()
    cache = _wiki_disk_cache()
    scores = score_wiki_pages(q, "", cache)
    ranked = select_ranked_pages(scores, cache, max_pages=4)
    pages_selected = [r[0] for r in ranked]
    idx = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""
    ctx = build_wiki_context(q, "", cache, max_pages_sent=4, max_wiki_tokens=3000, index_text=idx)
    user_block = f"{ctx}\n\n=== CUSTOMER QUESTION ===\n{q}"
    answer = ""
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": TEST_QUERY_SYSTEM},
                        {"role": "user", "content": user_block},
                    ],
                    "stream": False,
                    "options": {
                        "temperature": 0.15,
                        "num_predict": 4096,
                        "num_ctx": 8192,
                    },
                },
            )
            resp.raise_for_status()
            answer = (resp.json().get("message") or {}).get("content", "").strip()
    except httpx.ConnectError:
        answer = "❌ Cannot reach Ollama. Run: ollama serve"
    except Exception as e:
        answer = f"❌ Error: {e}"
    elapsed = round(time.time() - t0, 2)
    score_subset = {p: scores.get(p, 0) for p in sorted(scores, key=lambda k: -scores[k])[:12]}
    return {
        "pages_selected": pages_selected,
        "scores": score_subset,
        "answer": answer,
        "elapsed_seconds": elapsed,
    }


class TestVerdictBody(BaseModel):
    question: str
    verdict: str
    pages_selected: Optional[List[str]] = None
    notes: str = ""


@app.post("/api/test/verdict")
def api_test_verdict(body: TestVerdictBody):
    rec = {
        "ts": datetime.now().isoformat(),
        "question": body.question,
        "verdict": body.verdict,
        "pages_selected": body.pages_selected or [],
        "notes": body.notes,
    }
    prev = []
    if TEST_RESULTS_PATH.exists():
        try:
            prev = json.loads(TEST_RESULTS_PATH.read_text(encoding="utf-8"))
            if not isinstance(prev, list):
                prev = []
        except Exception:
            prev = []
    prev.append(rec)
    TEST_RESULTS_PATH.write_text(json.dumps(prev, indent=2), encoding="utf-8")
    return {"ok": True, "count": len(prev)}


# ── Serve Wiki Studio HTML ─────────────────────────────────────────────────

STUDIO_HTML_PATH = BASE_DIR / "static" / "studio.html"

@app.get("/", response_class=HTMLResponse)
def serve_studio():
    return STUDIO_HTML_PATH.read_text(encoding="utf-8")


# ── Run ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("\n" + "═"*50)
    print("  Wiki Studio — GSM Outdoors")
    print("═"*50)
    print(f"  Studio UI  →  http://localhost:8080")
    print(f"  Chat UI    →  http://localhost:8000  (run separately)")
    print(f"  Wiki dir   →  {WIKI_DIR}")
    print(f"  Raw dir    →  {RAW_DIR}")
    print("═"*50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
