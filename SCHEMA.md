# GSM Outdoors — Customer Support Wiki Schema

## About This Wiki

This wiki is the persistent knowledge base for **GSM Outdoors Customer Support**.
GSM Outdoors is an outdoor sports company managing customer support for multiple
hunting, fishing, and wireless camera brands.

The LLM maintains this wiki entirely. Humans read it and ask questions against it.
Raw source documents live in `raw/` and are never modified.

---

## Brands Covered

### Fishing
- **Phenix Rods** — fishing rods, warranty, Q&A, product guides
- **Dobyns Rods** — fishing rods, care instructions, warranty
- **Bucca Brand** — Trick Shad soft baits (6" and standard)
- **Bonehead Tackle** — baits, pro staff
- **Dealer Inquiry** — new dealer onboarding, credit apps, territory maps

### Hunting
- **WGI / Boss Buck** — wildlife feeders, timers, replacement parts
- **Avian-X** — hunting decoys (duck/turkey), A-frame blinds, replacement parts
- **Muddy / Hawk** — tree stands, ladder stands, hang-on stands
- **SOG Knives** — cutlery, engraving service, SKU decoder
- **Bloodsport** — ROC outserts for arrows
- **Box Blinds** — hunting blinds, replacement parts
- **Walker's** — ear protection / game ear products
- **BC (Bass Pro / Cabela's)** — retailer-specific procedures

### Wireless / Tech Support
- **Connect Cellular** — cellular trail cameras
- **Muddy MTRX** — cellular trail cameras
- **Stealth Cam STC-DS4KTM** — cellular trail cameras

---

## Wiki Page Conventions

Every wiki page MUST have this YAML frontmatter:

```yaml
---
title: "Page Title"
brands: [brand1, brand2]
category: fishing | hunting | wireless | operations
source_docs:
  - filename.pdf
  - filename.xlsx
last_updated: YYYY-MM-DD
related_pages:
  - "[[other-page]]"
  - "[[another-page]]"
---
```

### Content rules
- Write in plain markdown, agent-facing — answers should be directly usable by CS agents
- Use `## Heading` for major sections, `### Subheading` for sub-topics
- For warranty procedures: always include step-by-step numbered lists
- For parts lists: use markdown tables (Part Name | Part Number | Notes)
- For product comparisons: use markdown tables
- Cross-reference with `[[wiki-page-name]]` syntax
- Flag conflicts with: `> ⚠️ CONFLICT: this contradicts [[page#section]] — verify with source`
- Flag outdated info with: `> ⚠️ STALE: may be outdated — check raw source`

---

## Folder → Wiki Page Mapping

When ingesting a file, use its folder path to determine which wiki page(s) to update:

| Source folder / file | Primary wiki page | Also update |
|---|---|---|
| `App Support/Agent Call guidelines*` | `agent-operations.md` | `overview.md` |
| `App Support/GSM 2026 Holiday schedule*` | `agent-operations.md` | — |
| `CS FISHING/Fishing Training.docx` | `fishing/fishing-overview.md` | — |
| `CS FISHING/Rods - Phenix/` | `fishing/phenix-rods.md` | `fishing/fishing-overview.md` |
| `CS FISHING/Rods and Baits - Dobyns/` | `fishing/dobyns-rods.md` | `fishing/fishing-overview.md` |
| `CS FISHING/Baits - Bucca Brand/` | `fishing/bucca-brand.md` | `fishing/fishing-overview.md` |
| `CS FISHING/.../Bonehead Tackle/` | `fishing/bonehead-tackle.md` | — |
| `CS FISHING/Fishing Dealer Inquiry/` | `fishing/dealer-inquiry.md` | — |
| `CS HUNTING/Feeders & Timers/` | `hunting/feeders-and-timers.md` | `hunting/replacement-parts.md` |
| `CS HUNTING/Replacement Parts File/Avian-X Parts/` | `hunting/avian-x.md` | `hunting/replacement-parts.md` |
| `CS HUNTING/Replacement Parts File/` (non Avian-X) | `hunting/replacement-parts.md` | brand-specific pages |
| `CS HUNTING/SOG/` | `hunting/sog-knives.md` | — |
| `CS HUNTING/Comparison Docs/` | `hunting/product-comparisons.md` | brand-specific pages |
| `CS HUNTING/Procedures/BC*` | `hunting/procedures.md` | `agent-operations.md` |
| `GT - Wireless Tech Support/FAQS.docx` | `wireless/wireless-overview.md` | — |
| `GT - Wireless Tech Support/GSM Wireless CRM Procedures*` | `wireless/wireless-overview.md` | `agent-operations.md` |
| `GT - Wireless Tech Support/Wireless Responses-Zendesk*` | `wireless/wireless-overview.md` | — |
| `GT - Wireless Tech Support/HotBuy Upsell*` | `wireless/wireless-overview.md` | — |
| `GT - Wireless Tech Support/Camera Manuals/Connect*` | `wireless/connect-cellular.md` | `wireless/wireless-overview.md` |
| `GT - Wireless Tech Support/Camera Manuals/MUD-MTRX*` | `wireless/muddy-mtrx.md` | `wireless/wireless-overview.md` |
| `GT - Wireless Tech Support/Camera Manuals/STC-DS4KTM*` | `wireless/stealth-cam.md` | `wireless/wireless-overview.md` |

---

## Files to SKIP — Never Ingest

```
~$enix Q&A.docx                          # Office temp/lock file — not real content
GSMO Email Templates_*_OLD-DONOTUSE.docx # Explicitly marked outdated
HuntStand Tutorials (1).onepkg           # Unparseable OneNote package format
OneDrive_2024-04-19.zip                  # Duplicate of already-present files
.DS_Store                                # macOS metadata
```

---

## Deduplication Rules

- `Dobyns Warranty Information Form.pdf` appears in both Bucca Brand and Dobyns folders.
  Canonical location: `fishing/dobyns-rods.md#warranty`
  In `fishing/bucca-brand.md`, cross-link only: `See [[dobyns-rods#warranty]]`

- `WGI Feeder parts.docx` appears in both `Feeders & Timers/` and `Replacement Parts File/`.
  Canonical location: `hunting/feeders-and-timers.md`
  In `hunting/replacement-parts.md`, cross-link only.

- `Replacement Parts.xlsx` is the master list — it supersedes brand-specific part lists
  where they conflict. Always note the source file and date.

---

## Ingest Workflow

When told to ingest a file or folder:

1. **Parse** the file (text, tables as markdown, note any images)
2. **Identify** which wiki pages this file maps to (use the table above)
3. **Check** if those pages already exist
   - If new page: create it with frontmatter + content
   - If existing page: update only the sections this file adds to or contradicts
4. **Flag conflicts**: if new content contradicts existing content, add a `⚠️ CONFLICT` note
5. **Update `index.md`**: add/revise the entry for any page touched
6. **Append to `log.md`**:
   ```
   ## [YYYY-MM-DD] ingest | <filename> | pages updated: page1.md, page2.md
   ```
7. **Do not ingest** files from the skip list above

---

## Query Workflow

When a customer support agent asks a question:

1. Read `index.md` to identify the 2–4 most relevant wiki pages
2. Read those pages fully
3. Synthesize a clear, direct answer — write for a CS agent who needs to act immediately
4. Always cite: `[Source: wiki/page-name.md]` and `[Raw: original-filename.ext]`
5. If the answer requires a form or document, state its location in `raw/`
6. If a good answer required cross-page synthesis, offer to file it as a new wiki page

---

## Lint Workflow

When asked to lint the wiki, check for:

- Orphan pages (no inbound `[[links]]` from other pages)
- Pages with no `source_docs` frontmatter
- `⚠️ CONFLICT` markers that have not been resolved
- Brand names mentioned in content but lacking their own page
- Parts referenced across multiple pages without a canonical source
- Any page not listed in `index.md`

---

## index.md Format

```markdown
# GSM Outdoors CS Wiki — Index

Last updated: YYYY-MM-DD | Total pages: N

## Operations
| Page | Summary | Sources | Updated |
|---|---|---|---|
| [[agent-operations]] | Call guidelines, holiday schedule, BC procedures | 3 | YYYY-MM-DD |

## Fishing
| Page | Summary | Sources | Updated |
|---|---|---|---|
| [[fishing/fishing-overview]] | General fishing CS training | 1 | YYYY-MM-DD |
| [[fishing/phenix-rods]] | Phenix rod Q&A, warranty, product catalog | 5 | YYYY-MM-DD |
...

## Hunting
...

## Wireless
...
```

---

## log.md Format

Each entry starts with `## [YYYY-MM-DD]` for grep-ability:

```
## [2026-04-10] ingest | Phenix Q&A.docx | pages: fishing/phenix-rods.md, index.md
## [2026-04-10] ingest | Master Guide List - Phenix Rods.xlsx | pages: fishing/phenix-rods.md
## [2026-04-10] query | "how do I process a Phenix warranty?" | answered from fishing/phenix-rods.md
## [2026-04-10] lint | checked 14 pages | 2 orphans found, 1 conflict flagged
```
