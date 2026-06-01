# Wiki File Structure Guide — GSM Outdoors Chatbot

> This guide defines the required structure for all wiki markdown files.
> Following it ensures maximum retrieval accuracy with the local RAG pipeline.

---

## The Core Principle

Each `##` section is the unit of retrieval. The system embeds each section
independently, scores it against the user's query, and passes the top-scoring
sections to the model. Everything in the file structure flows from this fact.

---

## 1. File-Level Structure

```markdown
---
title: "Brand — Category Name"
brands: [Brand Name]
topics: [series name, alternate name, common question phrase, misspelling]
category: fishing
source_docs:
  - source_file.pdf
last_updated: YYYY-MM-DD
related_pages:
  - "[[category/related-page]]"
---

## [Series A], [Series B] — Overview

[2–4 sentences. Name every series covered on this page.
This section anchors page-level retrieval for broad queries.]

---
## [Series Name] — Overview & Best Uses

[Series-specific content. One series only.]

---
## [Series Name] — Model Table

[Table only. No Q&A here.]

---
## [Series Name] — Warranty & Replacement Cost

[Q&A pairs only. One series only.]

---
## [Series Name] — Pricing

[Q&A pairs only. One series only.]

---
## [Series A] vs [Series B] — Comparison

[Q&A pairs only. Named comparison — never inside a single-series section.]
```

---

## 2. Section Heading Rules (Most Important)

**Always name the series in the heading. Never use generic headings.**

| Wrong | Right |
|---|---|
| `## Questions — Pricing & Warranty` | `## Iron Feather — Pricing & Warranty` |
| `## Questions — Specs & Models` | `## Cicada, Trifecta — Specs & Models` |
| `## Questions — Choosing the Right Series` | `## M1 Bass, Feather, K2 — Choosing the Right Series` |
| `## Overview` (on a multi-series page) | `## Cicada, Trifecta, Trifecta Pro, Trifecta Lite — Overview` |

**Why:** The heading is embedded as part of the section text. A heading that
says "Iron Feather" produces a vector close to queries about the Iron Feather.
A heading that says "Questions — Pricing & Warranty" produces a vector that is
nearly identical across all pages — the wrong page wins retrieval.

**One series per section when the content is series-specific.** If you have
pricing for Iron Feather and pricing for Elixir, they get two separate sections,
not one combined section.

---

## 3. Section Size Rules

The embedding only uses the first **1500 characters** of each section. Any Q&A
added after that point is invisible to retrieval.

- **Target:** Keep each section under **800 characters of content** (not counting the heading)
- **Maximum:** 1400 characters before the embedding cuts off
- If a section needs to be longer, **split it into two sections** with distinct headings

Check section length before adding content:

```bash
echo -n "your section content here" | wc -c
```

---

## 4. Q&A Format

Every answer should be a standalone `**Question?**` / `Answer.` pair.
The model copies or closely paraphrases what it reads — write the answer
the way you want it delivered to the customer.

### Rules for Q&A Pairs

**Use the exact phrasing a customer would use in the question:**

```markdown
**How much does it cost to replace an Iron Feather?**
The Iron Feather is Tier-5 — replacement fee: $165. Return shipping: $20
(rods 7'10" and under) or $50 (rods 7'11" and over). Total: $185–$215.
```

**Add multiple question variants for the same fact:**

```markdown
**How much does it cost to replace an Iron Feather?**
**What is the Iron Feather replacement fee?**
**Iron Feather warranty cost?**
The Iron Feather is Tier-5 — replacement fee: $165...
```

**State every fact explicitly — never imply or rely on inference:**

| Wrong (implied) | Right (explicit) |
|---|---|
| "Unlike the Trifecta, the Pro adds Essex SiC guides" | "The Trifecta Pro has Essex SiC guides. The original Trifecta does not." |
| "Available in multiple lengths" | "Available in 7'1\", 7'7\", 7'2\", 7'9\", 8'6\"" |
| "See tier fees page for details" | "Replacement fee: $165. Shipping: $20 under 7'10\", $50 over 7'11\"." |

**Include numbers, model codes, and prices inline.** The model cannot follow
wikilinks or navigate to another page during a response.

**End with a deflection when the answer is incomplete without support contact:**

```markdown
For current pricing, verify at phenixrods.com before quoting a customer.
```

---

## 5. Comparison Sections

Comparisons are among the most common customer questions and the hardest to
retrieve correctly without dedicated sections.

```markdown
## Iron Feather vs Elixir — Comparison

**What is the difference between the Iron Feather and the Elixir?**
The Iron Feather is the premium flagship: extra-fast action, 1–9 lb, Fuji
Titanium SiC guides, $549–$579. The Elixir is the fly-rod-inspired all-arounder:
fast action, 1–10 lb, wider length range (6'–8'6"), $60–$270. Choose Iron Feather
for maximum sensitivity; choose Elixir for versatility and value.

**Which is better for trout streams, the Iron Feather or the Elixir?**
[Self-contained answer.]
```

**Never put a comparison Q&A inside a single-series section.** It dilutes that
section's embedding away from single-series queries.

---

## 6. "I Don't Know" Entries

Add explicit deflection Q&As for out-of-scope questions. This prevents the local
model from hallucinating an answer when no relevant section is found.

```markdown
**Does Phenix make fly rods?**
Phenix does not currently offer dedicated fly rods. For recommendations,
visit phenixrods.com or contact support.

**Does Phenix make ice fishing rods?**
Phenix does not currently offer ice fishing rods. Contact phenixrods.com
for the latest product lineup.
```

Place these in the Overview section of the most relevant page, or in the main
`phenix-rods.md` page.

---

## 7. Topics Frontmatter

The `topics:` list is used in keyword fallback retrieval when the vector store
is unavailable. Keep it comprehensive.

```yaml
topics: [
  iron feather,          # exact series name
  IF-S,                  # model code prefix
  ultralight rod,        # category term
  trout rod,             # use case
  1-9 lb spinning,       # spec phrase
  replace iron feather,  # action phrase
  iron feather cost,     # pricing phrase
  iron feather warranty  # warranty phrase
]
```

Include: series names, model code prefixes, use-case terms, spec phrases,
action phrases, and common misspellings.

---

## 8. Full Page Template

```markdown
---
title: "Phenix Rods — [Category]"
brands: [Phenix Rods]
topics: [series1, series2, key phrase, key phrase]
category: fishing
source_docs:
  - source.pdf
last_updated: YYYY-MM-DD
related_pages:
  - "[[fishing/phenix-rods-warranty]]"
  - "[[fishing/phenix-rods-tier-fees]]"
---

## [Series1], [Series2] — Overview

Brief description naming all series on this page. 2–4 sentences.

---
## [Series1] — Overview & Best Uses

**Best for:** [one sentence]

[2–3 sentences of description]

---
## [Series1] — Model Table

| Model | Length | Line | Action | Lure Weight |
|---|---|---|---|---|
| ... | | | | |

---
## [Series1] — Specs & Models

**What line weight is the [Series1] rated for?**
[Explicit answer with numbers.]

**What is the longest [Series1] available?**
[Explicit answer.]

---
## [Series1] — Warranty & Replacement Cost

**How much does it cost to replace a [Series1]?**
The [Series1] is [Tier-N] — replacement fee: $[X]. Return shipping: $[Y]
(rods [length] and under) or $[Z] (rods over [length]). Total: $[A]–$[B].

**What warranty tier is the [Series1]?**
Tier-[N] ($[X] replacement fee). See [[fishing/phenix-rods-tier-fees]]
for full procedure.

---
## [Series1] — Pricing

**How much does the [Series1] cost?**
Complete rods: $[X]–$[Y]. Blanks: $[A]–$[B].
Verify current pricing at phenixrods.com.

---
## [Series1] vs [Series2] — Comparison

**What is the difference between [Series1] and [Series2]?**
[Self-contained comparison with all relevant facts inline.]
```

---

## 9. Quick Checklist for Every New Section

Before saving any new section, verify:

- [ ] Heading names the specific series (not generic "Questions — Pricing")
- [ ] Section is under 1400 characters total
- [ ] Every fact is stated explicitly — no implied or inferred information
- [ ] Prices, model codes, and specs are inline — not "see other page"
- [ ] Questions are phrased the way a customer would actually ask them
- [ ] Series-specific content is not mixed with other series in the same section
- [ ] Comparisons between series have their own dedicated section
- [ ] Out-of-scope questions have explicit deflection answers

---

## 10. What Breaks Retrieval — Reference

| Problem | Symptom | Fix |
|---|---|---|
| Generic heading | Wrong page retrieved for series-specific query | Name the series in the heading |
| Q&A added after 1500 chars | Q&A never retrieved | Split section or move Q&A to a new dedicated section |
| Multi-series section | Score too low for any specific series query | One series per section |
| Comparison inside single-series section | Series embedding diluted | Move comparison to its own `## A vs B` section |
| Fact implied rather than stated | Model hallucinates the missing fact | Write the fact explicitly in the answer |
| No deflection entry | Model invents an answer for out-of-scope question | Add explicit "we don't carry X" Q&A |
