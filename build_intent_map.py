#!/usr/bin/env python3
"""
build_intent_map.py — Hierarchical support-intelligence intent map builder.

v2 schema — each section now produces:
  Existing (v1):  pillars, question_variants
  New (v2):       canonical_intent, micro_intents, support_entities,
                  procedural_keywords, customer_phrasing, semantic_aliases,
                  workflow_stage, required_inputs, escalation_signals,
                  hierarchical_intent

Output: wiki/intent_map.json
  Incremental — already-processed sections are skipped. Re-run safely.
  Use --rebuild to regenerate all sections with the v2 schema.

Usage:
    python build_intent_map.py                                         # incremental update
    python build_intent_map.py --rebuild                               # full v2 rebuild
    python build_intent_map.py --page fishing/phenix-rods-warranty.md # one page only
    python build_intent_map.py --dry-run                               # list sections, no API calls
    python build_intent_map.py --show                                  # summary table
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).resolve().parent
WIKI_DIR       = BASE_DIR / "wiki"
INTENT_MAP_PATH = WIKI_DIR / "intent_map.json"

SKIP_PAGES  = {"index.md", "log.md", "lint-report.md"}
BATCH_SIZE  = 5    # v2 prompt is ~3× larger than v1 — keep batches small to avoid timeouts
# BATCH_SIZE  = 15  # v1 value — too large for v2 prompt + output

# ── v1 — Intent types (closed vocabulary, preserved for backward compat) ──────
INTENT_TYPES = [
    "product_info",        # what is X, specs, overview
    "warranty_claim",      # how to file / submit warranty
    "replacement_process", # how to get a replacement, cut logo section, mail in
    "pricing",             # cost, MSRP, tier fees
    "care_maintenance",    # cleaning, storage, free parts
    "comparison",          # X vs Y, difference between
    "troubleshooting",     # broken, not working, error
    "returns",             # return, refund, send back
    "availability",        # do you carry, does brand make
    "how_to_use",          # technique, setup, rigging
    "general",             # catch-all
]

# ── v2 — Extended vocabulary for support-intelligence retrieval ───────────────

CANONICAL_INTENTS = [
    # Replacement & warranty flows
    "no_hassle_replacement",         # standard no-hassle program submission
    "manufacturing_defect_claim",    # defect within first few casts / normal use
    "boron_legacy_claim",            # legacy rod replacement program
    "dealer_warranty_process",       # process warranty through a retailer/dealer
    "second_owner_replacement",      # non-original owner making a claim
    "replacement_upgrade_request",   # swap broken rod for a different/higher model
    # Fee & cost
    "tier_fee_question",             # what does it cost to replace this rod
    "return_shipping_fee",           # cost to ship the rod back
    "upgrade_fee_calculation",       # additional fee when upgrading model
    "replacement_cost_breakdown",    # full cost breakdown (tier + shipping + tax)
    # Process steps
    "logo_section_requirement",      # cut the 4–6 inch logo section above reel seat
    "warranty_form_submission",      # complete and submit the warranty application
    "mailing_instructions",          # where and how to physically mail the claim
    "shipping_timeline",             # how long until replacement arrives
    "payment_instructions",          # how to pay (check, money order, credit card)
    "photo_submission_requirement",  # submit photos of break point for defect review
    # Eligibility
    "warranty_eligibility_check",    # am I covered / do I qualify
    "coverage_exclusions",           # what voids or is excluded from warranty
    "proof_of_purchase_requirement", # do I need a receipt or proof of purchase
    "original_owner_requirement",    # warranty requires original buyer
    # Product knowledge
    "product_specifications",        # specs, models, weights, lengths
    "product_comparison",            # X vs Y — which should I choose
    "product_recommendation",        # what should I buy for my use case
    "model_availability",            # does brand make a rod/product for X
    "product_pricing",               # MSRP, catalog price
    # Support operations
    "free_parts_request",            # get a free replacement tip or guide
    "troubleshooting_guide",         # diagnose broken / not-working product
    "care_and_maintenance",          # cleaning, storage, maintenance
    "return_refund_process",         # return product for refund
    "general_info",                  # catch-all / informational
]

WORKFLOW_STAGES = [
    "awareness",     # customer doesn't know what to do or what program exists
    "eligibility",   # checking whether they qualify
    "documentation", # gathering required docs, photos, receipts
    "submission",    # actively sending in a claim or request
    "payment",       # paying fees, cost confirmation
    "fulfillment",   # waiting for or tracking a replacement
    "exception",     # edge case — exclusion, escalation, special handling
    "general",       # doesn't map to a specific stage
]

MICRO_INTENT_TYPES = [
    "eligibility_check",        # does this customer qualify
    "logo_section_requirement", # cut logo section instruction
    "photo_requirement",        # submit break-point photos
    "payment_requirement",      # fee must accompany the claim
    "shipping_instructions",    # how to package and ship
    "mailing_address",          # where to send the claim
    "timeline_expectation",     # how long it takes
    "upgrade_option",           # can they upgrade to a different model
    "fee_explanation",          # breakdown of what the fee covers
    "exclusion_notice",         # what is NOT covered
    "required_documents",       # full list of required items
    "escalation_path",          # who to contact for exceptions
    "exception_handling",       # special case / edge case logic
    "product_specs",            # dimensions, weight, line rating
    "comparison_criteria",      # what to compare when choosing
    "recommendation_logic",     # how to decide which product
    "availability_check",       # does this product/model exist
    "care_instruction",         # how to clean, store, maintain
    "troubleshooting_step",     # diagnostic or fix step
    "return_eligibility",       # conditions for returning
    "contact_info",             # phone, email, address
    "coverage_scope",           # what the warranty/policy covers
]


# ── Section chunker (mirrors vector_store.py logic exactly) ───────────────────

def _strip_frontmatter(content: str) -> str:
    if not content.strip().startswith("---"):
        return content
    end = content.find("\n---\n", 3)
    return content[end + 5:] if end != -1 else content


def _chunk_page(rel_path: str, content: str) -> list[dict]:
    """Split a wiki page into ## sections. Mirrors vector_store._chunk_page."""
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
        chunks.append({
            "rel_path":        rel_path,
            "page_title":      page_title,
            "section_heading": heading,
            "text":            text,
        })

    return chunks


# ── OpenAI call ───────────────────────────────────────────────────────────────

def _call_openai(api_key: str, page_title: str, rel_path: str, sections: list[dict]) -> list[dict]:
    """
    Send a batch of sections to OpenAI and return v2 support-intelligence metadata.
    Uses raw httpx — no openai package required (matches app_v3.py pattern).

    v2 schema per section:
      Existing: pillars, question_variants
      New:      canonical_intent, micro_intents, support_entities,
                procedural_keywords, customer_phrasing, semantic_aliases,
                workflow_stage, required_inputs, escalation_signals,
                hierarchical_intent
    """
    sections_block = ""
    for i, s in enumerate(sections, 1):
        sections_block += (
            f"\n[SECTION {i}]\n"
            f"Heading: {s['section_heading']}\n"
            f"Content preview: {s['text'][:500]}\n"
        )

    # ── v1 prompt (preserved for reference) ──────────────────────────────────
    # prompt_v1 = f"""You are analyzing wiki sections from a customer service
    # knowledge base for GSM Outdoors...
    # For EACH section produce: pillars (4 fields) + 12 question_variants.
    # (original simple prompt — replaced by v2 below)"""

    # ── v2 prompt — support-intelligence retrieval schema ────────────────────
    prompt = f"""You are a senior customer support knowledge engineer analyzing wiki sections for GSM Outdoors — an outdoor sporting goods distributor handling customer support for fishing rods (Phenix Rods, Dobyns Rods, Bucca Brand, Bonehead Tackle), hunting gear, and wireless trail cameras.

Your task: extract rich support-intelligence metadata so a conversational retrieval system can match customer queries — including emotionally phrased, informal, and Zendesk-style support tickets — to the right wiki section.

PAGE: {rel_path}
PAGE TITLE: {page_title}
{sections_block}

For EACH section, return ALL fields below.

━━ EXISTING FIELDS (v1 — required, schema unchanged) ━━

pillars:
  category   — one of [fishing, hunting, wireless, general]
  brand      — exact brand name ("Phenix Rods", "Dobyns Rods", "Bucca Brand", "Bonehead Tackle") or null
  product    — product type ("spinning rod", "casting rod", "swimbait", "trail camera") or null
  intent_type — one of {json.dumps(INTENT_TYPES)}

question_variants — exactly 12 customer-language questions this section directly answers.
  RULES (v2 upgrade — more conversational than v1):
  - Prioritize emotional, informal, conversational phrasing over documentation language
  - Include: panicked ("my rod just snapped!"), confused ("what do I even do now?"), casual ("do I have to cut the rod?"), indirect ("if my rod breaks am I covered?")
  - Include 3 Zendesk-style openers: "Hi, I need help with...", "I just noticed...", "Can someone explain..."
  - Include 2 variants with NO brand name (customer forgot to mention it)
  - Include 1 variant with a specific model number or product name if the section mentions one
  - Do NOT write documentation-style variants like "manufacturing defect claim submission process"

━━ NEW FIELDS (v2 schema) ━━

canonical_intent — the single most specific support workflow this section serves.
  Must be one of: {json.dumps(CANONICAL_INTENTS)}

micro_intents — all procedural sub-intents present in this section (list, 1–6 items).
  Use only values from: {json.dumps(MICRO_INTENT_TYPES)}

support_entities — structured facts extracted from the section:
  fee_amounts      — dollar amounts (e.g. ["$165", "$20"])
  model_numbers    — specific model numbers or SKUs mentioned
  required_documents — what the customer must provide
  mailing_address  — shipping address string or null
  time_estimates   — turnaround times (e.g. ["2 weeks"])
  phone_number     — phone number or null
  conditions       — eligibility conditions (e.g. ["original owner only", "rod purchased from authorized dealer"])

procedural_keywords — 5–10 technical/operational terms a support agent would search.
  Examples: ["cut logo section", "tier 5", "5250 Frye Rd", "padded envelope", "no-hassle replacement", "warranty form"]

customer_phrasing — 8–12 raw informal expressions a customer might use (short phrases, NOT full questions).
  Examples: ["my rod snapped", "broke after one cast", "what do I cut off", "how much will it cost me", "do I need a receipt", "can I send the whole rod"]

semantic_aliases — 4–8 mappings from casual customer language to canonical support terminology.
  Format: [{{"customer_term": "...", "canonical_term": "..."}}]
  Examples:
    {{"customer_term": "snapped", "canonical_term": "broken rod"}}
    {{"customer_term": "what do I owe", "canonical_term": "replacement fee"}}
    {{"customer_term": "cut off part of rod", "canonical_term": "logo section requirement"}}
    {{"customer_term": "send the whole rod", "canonical_term": "logo section only — do not ship full rod"}}

workflow_stage — primary customer journey stage this section serves.
  Must be one of: {json.dumps(WORKFLOW_STAGES)}

required_inputs — what a customer must have or complete before this section applies.
  Examples: ["cut logo section (4-6 inches above reel seat)", "completed warranty application form", "$165 tier-5 payment", "photos of break point"]

escalation_signals — phrases or situations indicating the customer needs special handling or agent intervention.
  Examples: ["wants to send whole rod", "international address", "no receipt", "second owner", "rod over 7 feet 10 inches"]

hierarchical_intent — the full 4-level intent hierarchy:
  l1_category      — fishing | hunting | wireless | general
  l2_intent_type   — from INTENT_TYPES
  l3_canonical_intent — from CANONICAL_INTENTS
  l4_micro_intents — list from MICRO_INTENT_TYPES

━━ OUTPUT ━━

Return a JSON object with key "sections", one element per section:
{{
  "sections": [
    {{
      "section_heading": "<exact heading from above>",
      "pillars": {{"category": "...", "brand": "...", "product": "...", "intent_type": "..."}},
      "question_variants": ["q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8", "q9", "q10", "q11", "q12"],
      "canonical_intent": "...",
      "micro_intents": ["...", "..."],
      "support_entities": {{"fee_amounts": [], "model_numbers": [], "required_documents": [], "mailing_address": null, "time_estimates": [], "phone_number": null, "conditions": []}},
      "procedural_keywords": ["...", "..."],
      "customer_phrasing": ["...", "..."],
      "semantic_aliases": [{{"customer_term": "...", "canonical_term": "..."}}],
      "workflow_stage": "...",
      "required_inputs": ["...", "..."],
      "escalation_signals": ["...", "..."],
      "hierarchical_intent": {{"l1_category": "...", "l2_intent_type": "...", "l3_canonical_intent": "...", "l4_micro_intents": ["..."]}}
    }}
  ]
}}"""

    with httpx.Client(timeout=180) as http:  # 180s — v2 output is large; was 120s for v1
        resp = http.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model":           "gpt-4o-mini",
                "messages":        [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature":     0.7,
            },
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON parse error on {rel_path}: {e}")
        return []

    # Normalize — handle both {"sections": [...]} and flat list responses
    if isinstance(data, list):
        result = data
    elif "sections" in data and isinstance(data["sections"], list):
        result = data["sections"]
    else:
        for v in data.values():
            if isinstance(v, list):
                result = v
                break
        else:
            print(f"  [WARN] Unexpected response structure for {rel_path}")
            return []

    for item in result:
        if not isinstance(item, dict):
            continue  # skip strings/nulls OpenAI occasionally inserts into the array
        item["rel_path"] = rel_path

    return [item for item in result if isinstance(item, dict)]


# ── Map I/O ───────────────────────────────────────────────────────────────────

def _load_map() -> list[dict]:
    if INTENT_MAP_PATH.exists():
        try:
            return json.loads(INTENT_MAP_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_map(entries: list[dict]) -> None:
    INTENT_MAP_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _make_key(rel_path: str, heading: str) -> str:
    return f"{rel_path}||{heading}"


def _norm_heading(h: str) -> str:
    """Normalize heading for fuzzy matching — collapses em-dash/en-dash/hyphen variants."""
    return re.sub(r'\s*[—–\-]+\s*', ' ', h).lower().strip()


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_dry_run(page_filter: Optional[str] = None) -> None:
    """List all sections that would be processed without calling the API."""
    pages = sorted(WIKI_DIR.rglob("*.md"))
    total = 0
    for p in pages:
        if p.name in SKIP_PAGES:
            continue
        rel = p.relative_to(WIKI_DIR).as_posix()
        if page_filter and rel != page_filter:
            continue
        chunks = _chunk_page(rel, p.read_text(encoding="utf-8"))
        if not chunks:
            continue
        print(f"\n{rel}  ({len(chunks)} sections)")
        for c in chunks:
            print(f"  ## {c['section_heading'][:80]}")
            total += 1
    print(f"\nTotal: {total} sections across all pages")


def cmd_show() -> None:
    """Print a summary table of the current intent map."""
    entries = _load_map()
    if not entries:
        print("intent_map.json is empty or does not exist.")
        return
    # Detect whether any v2 fields are present
    has_v2 = any("canonical_intent" in e for e in entries)
    if has_v2:
        print(f"{'rel_path':<50} {'section_heading':<45} {'canonical_intent':<30} {'stage':<14} {'mi':<4} var")
        print("-" * 150)
        for e in entries:
            p   = e.get("pillars", {})
            rel = e.get("rel_path", "")[:49]
            hdg = e.get("section_heading", "")[:44]
            ci  = e.get("canonical_intent", p.get("intent_type", ""))[:29]
            ws  = e.get("workflow_stage", "")[:13]
            nmi = len(e.get("micro_intents", []))
            nv  = len(e.get("question_variants", []))
            print(f"{rel:<50} {hdg:<45} {ci:<30} {ws:<14} {nmi:<4} {nv}")
    else:
        # v1 display
        print(f"{'rel_path':<55} {'section_heading':<55} {'intent_type':<22} variants")
        print("-" * 150)
        for e in entries:
            p   = e.get("pillars", {})
            rel = e.get("rel_path", "")[:54]
            hdg = e.get("section_heading", "")[:54]
            it  = p.get("intent_type", "")[:21]
            nv  = len(e.get("question_variants", []))
            print(f"{rel:<55} {hdg:<55} {it:<22} {nv}")
    print(f"\n{len(entries)} entries total  ({'v2 schema' if has_v2 else 'v1 schema'})")


def cmd_build(
    page_filter: Optional[str] = None,
    rebuild: bool = False,
) -> None:
    """Build or update the intent map."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        # Try to load from .env in project root
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"\'')
                    break
    if not api_key:
        print("OPENAI_API_KEY not set. Export it or put it in .env")
        sys.exit(1)

    existing    = [] if rebuild else _load_map()
    existing_keys = {_make_key(e["rel_path"], e["section_heading"]) for e in existing}

    pages = sorted(WIKI_DIR.rglob("*.md"))
    total_new = 0
    total_skipped = 0

    for p in pages:
        if p.name in SKIP_PAGES:
            continue
        rel = p.relative_to(WIKI_DIR).as_posix()
        if page_filter and rel != page_filter:
            continue

        chunks = _chunk_page(rel, p.read_text(encoding="utf-8"))
        if not chunks:
            continue

        new_chunks = [
            c for c in chunks
            if rebuild or _make_key(c["rel_path"], c["section_heading"]) not in existing_keys
        ]
        skipped = len(chunks) - len(new_chunks)
        total_skipped += skipped

        if not new_chunks:
            print(f"  skip  {rel}  (all {len(chunks)} sections already mapped)")
            continue

        n_batches = (len(new_chunks) + BATCH_SIZE - 1) // BATCH_SIZE
        print(
            f"  processing  {rel}  "
            f"({len(new_chunks)} new, {skipped} already done"
            f"{', ' + str(n_batches) + ' batches' if n_batches > 1 else ''}) ...",
            end="", flush=True,
        )

        # Split into batches so large pages don't exceed output-token limits
        all_results: list[dict] = []
        batch_error = False
        for i in range(0, len(new_chunks), BATCH_SIZE):
            batch = new_chunks[i : i + BATCH_SIZE]
            try:
                all_results.extend(_call_openai(api_key, chunks[0]["page_title"], rel, batch))
            except Exception as e:
                print(f"\n  [ERROR] batch {i//BATCH_SIZE + 1}/{n_batches}: {e}")
                batch_error = True
                # Continue to next batch — don't abort the whole page

        # Match results back to sections by heading
        # Build two lookup dicts: exact heading and normalized heading
        result_by_heading      = {r.get("section_heading", ""): r for r in all_results}
        result_by_norm_heading = {_norm_heading(r.get("section_heading", "")): r for r in all_results}

        added = 0
        for chunk in new_chunks:
            hdg  = chunk["section_heading"]
            # 1. Exact match
            match = result_by_heading.get(hdg)
            # 2. Normalized (dash variant) match
            if not match:
                match = result_by_norm_heading.get(_norm_heading(hdg))
            # 3. Substring fallback
            if not match:
                hdg_n = _norm_heading(hdg)
                for rh_n, rv in result_by_norm_heading.items():
                    if rh_n in hdg_n or hdg_n in rh_n:
                        match = rv
                        break

            if not match:
                print(f"\n  [WARN] No result for section: '{hdg[:60]}'")
                continue

            # Strip non-string values — inch marks (5'8") in content can corrupt
            # the JSON array; OpenAI pads remaining slots with -0.5 floats
            raw_variants = match.get("question_variants", [])
            clean_variants = [v for v in raw_variants if isinstance(v, str) and v.strip()]
            if len(clean_variants) < 8:
                print(f"\n  [WARN] Too few valid variants for '{hdg[:50]}' ({len(clean_variants)}) — will retry on next run")
                continue

            entry = {
                # ── v1 fields (backward compatible) ──────────────────────────
                "rel_path":          rel,
                "section_heading":   hdg,
                "pillars":           match.get("pillars", {}),
                "question_variants": clean_variants,
                # ── v2 fields (support-intelligence schema) ──────────────────
                "canonical_intent":    match.get("canonical_intent", "general_info"),
                "micro_intents":       match.get("micro_intents", []),
                "support_entities":    match.get("support_entities", {}),
                "procedural_keywords": match.get("procedural_keywords", []),
                "customer_phrasing":   match.get("customer_phrasing", []),
                "semantic_aliases":    match.get("semantic_aliases", []),
                "workflow_stage":      match.get("workflow_stage", "general"),
                "required_inputs":     match.get("required_inputs", []),
                "escalation_signals":  match.get("escalation_signals", []),
                "hierarchical_intent": match.get("hierarchical_intent", {}),
            }
            existing.append(entry)
            existing_keys.add(_make_key(rel, hdg))
            added += 1

        total_new += added
        print(f" done ({added} entries added)")

        # Save after every page so progress survives interruptions
        _save_map(existing)

        # Polite pause between pages to avoid rate-limit bursts
        time.sleep(0.5)

    print(f"\nDone. {total_new} new entries added, {total_skipped} skipped.")
    print(f"Map saved to: {INTENT_MAP_PATH}")
    print(f"Total entries: {len(existing)}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a query intent map from wiki sections."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List sections without calling the API"
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Print the current intent map as a table"
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Re-process all sections even if already in the map"
    )
    parser.add_argument(
        "--page", metavar="REL_PATH",
        help="Process only this page (e.g. fishing/phenix-rods-warranty.md)"
    )
    args = parser.parse_args()

    if args.show:
        cmd_show()
    elif args.dry_run:
        cmd_dry_run(page_filter=args.page)
    else:
        cmd_build(page_filter=args.page, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
