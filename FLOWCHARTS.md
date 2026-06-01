# GSM Outdoors Chatbot — System Flowcharts

---

## Flowchart 1 — Original Version (gsm_support_demo_backup_2026-04-24)

```mermaid
flowchart TD
    A([User sends message]) --> B{"Starts with /reload?"}
    B -- Yes --> C["Reload wiki from disk\nReturn confirmation"] --> Z
    B -- No --> D[Append message to history]

    D --> E[Build history_text from last 6 messages]

    E --> F[score_wiki_pages\nKeyword matching only]

    F --> F1["For each wiki page:\n3pts if keyword in query\n1pt if keyword in history"]

    F1 --> G[select_ranked_pages\nTop 4 pages by score]

    G --> H{Any pages scored?}
    H -- No --> I["Fallback: fishing-overview.md\nwireless-overview.md"]
    H -- Yes --> J

    I --> J["Truncate each page:\ncontent flat cut at 3000 chars"]

    J --> K["Build context block:\nWiki Index prefix\ntop 4 page contents"]

    K --> L["Build system prompt:\nPersonality rules\nAccuracy rules\nClarification rules\nWiki context appended"]

    L --> M{Selected model?}
    M -- OpenAI --> N["POST openai/v1/chat\ntemp 0.15\nStream tokens"]
    M -- Claude API --> O["POST anthropic/v1/messages\ntemp 0.15\nStream tokens"]
    M -- Ollama --> P["POST ollama/api/chat\ntemp 0.15 num_predict 1024\nStream tokens"]
    M -- All Models --> Q["Run all 4 models\nin parallel asyncio.gather\nDisplay 4-column table"]

    N --> R["Append perf stats to response\nAppend sources if show_sources ON"]
    O --> R
    P --> R
    Q --> S["Specificity score\nCount model numbers in output\nStar highest-scoring model"]
    S --> R

    R --> T["Append full response including\ndebug suffix to history"]
    T --> U[Trim history to 20 messages]
    U --> V["Save history to session\nLog query to wiki/log.md"]
    V --> Z([End turn])

    style A fill:#4a90d9,color:#fff
    style Z fill:#4a90d9,color:#fff
    style F fill:#f5a623,color:#fff
    style G fill:#f5a623,color:#fff
    style L fill:#7ed321,color:#fff
    style C fill:#9b59b6,color:#fff

    classDef weakness fill:#e74c3c,color:#fff
    class J weakness
```

### Key Weaknesses in Original Version

| Component | Problem |
|---|---|
| Retrieval | Keyword-only — "lightest steelhead rod" matches nothing if "steelhead" not in keyword list |
| Page truncation | `content[:3000]` flat cut — Q&As at the bottom of a page are always lost |
| No brand gate | System prompt tells LLM to ask about brand, but no programmatic enforcement |
| No brand scoping | All 20+ pages scored on every query — fishing pages compete with hunting pages |
| No multi-turn memory | If LLM asks "which brand?" and user replies "Phenix", next retrieval runs on "Phenix" not the original question |
| Generic wiki headings | `## Questions — Pricing & Warranty` on every page — near-identical embeddings across all pages |
| MAX_WIKI_TOKENS = 3000 | Frequently cuts off correct answers mid-page |

---

## Flowchart 2 — Current Version (gsm_deploy_pkg)

```mermaid
flowchart TD
    A([User sends message]) --> B{Slash command?}

    B -- Yes --> CMD{Which command?}
    CMD -- reload --> C1[Reload wiki pages from disk]
    CMD -- reset --> C2[Clear history and reset brand gate]
    CMD -- pages --> C3[List all loaded wiki pages]
    CMD -- search --> C4["Run get_top_sections\nShow scores in chat"]
    CMD -- read --> C5["Display wiki page\nin side panel"]
    CMD -- wrong --> C6["Log wrong answer\nto wiki/log.md"]
    CMD -- speed --> C7[Show last response perf stats]
    C1 & C2 & C3 & C4 & C5 & C6 & C7 --> Z

    B -- No --> D[Append message to history]

    D --> E["BRAND GATE — brand_gate.py"]

    E --> GS{gate_state?}

    GS -- AWAIT_BRAND --> GB["resolve_brand_answer\nMatch user reply to brand list"]
    GB --> GB2{Brand resolved?}
    GB2 -- No --> GB3["Re-ask brand question\nReturn early"] --> Z
    GB2 -- Yes --> GB4["Confirm brand in session\nWalk back history to recover\noriginal query before brand reply"]
    GB4 --> HINT

    GS -- AWAIT_CATEGORY --> GC["resolve_category_answer\nMatch user reply to category"]
    GC --> GC2{Category resolved?}
    GC2 -- No --> GC3["Re-ask category question\nReturn early"] --> Z
    GC2 -- Yes --> GC4["Save category\nSet state to AWAIT_BRAND\nAsk brand question\nReturn early"] --> Z

    GS -- CONFIRMED --> GK["Skip detection entirely\nUse confirmed_brand from session"]
    GK --> HINT

    GS -- IDLE --> GD["detect_brand\nScan query plus aliases plus brand_map.yaml"]
    GD --> GD2{Needs clarification?}
    GD2 -- Yes --> GD3["Ask clarifying question\nSet state to AWAIT_BRAND\nor AWAIT_CATEGORY\nReturn early"] --> Z
    GD2 -- No --> GD4["Confirm brand and category\nSet state to CONFIRMED"]
    GD4 --> HINT

    HINT["Map confirmed_brand to\nbrand_hint path prefix\nexample: fishing/phenix-rods"]

    HINT --> RQ["Build retrieval query\nUsing original question\nnot the brand reply"]

    RQ --> VS{Vector store ready?}

    VS -- Yes --> SEC["get_top_sections\nSection-level semantic retrieval"]

    SEC --> SEC1["Embed query via\nnomic-embed-text on Ollama"]
    SEC1 --> SEC2["Cosine similarity against\nall section embeddings in SQLite"]
    SEC2 --> SEC3["Filter by category\nfishing or hunting or wireless\nApply brand_hint if confirmed"]
    SEC3 --> SEC4["Top 12 sections\nMin score 0.60"]
    SEC4 --> SEC5{Any sections found?}

    SEC5 -- Yes --> BCS["build_context_from_sections\nGroup by page top 4\nExtract section text"]
    BCS --> CTX

    SEC5 -- No --> FB

    VS -- No --> FB["FALLBACK: Keyword-only\nmerge_page_keywords\nscore_wiki_pages"]
    FB --> FB2["brand match 5pts\ntopic match 3pts\nbody confirmation 2pts\nhistory match 1pt"]
    FB2 --> FB3["select_ranked_pages\nBrand gate and category gate\nOverview demotion\nTop 4 pages"]
    FB3 --> FB4["extract_relevant_sections\nWord overlap scoring per section\nPin procedure sections\nFill budget intelligently"]
    FB4 --> CTX

    CTX["Wiki context block\nmax 5000 tokens\nPlus wiki index excerpt"]

    CTX --> SYS["Build system prompt:\nPersonality and Accuracy rules\nWarranty rules\nWiki context\nSCHEMA reference"]

    SYS --> MOD{Selected model?}

    MOD -- OpenAI --> N["POST openai/v1/chat\ntemp 0.15\nStream tokens"]
    MOD -- Groq --> NG["POST groq/chat\ntemp 1.0\nStream tokens"]
    MOD -- gemma4:e4b --> P1["POST ollama/api/chat\ntemp 0.15 num_predict 4096\nrepeat_penalty 1.1"]
    MOD -- qwen3.5:9b --> P2["POST ollama/api/chat\ntemp 0.15 num_predict 4096\nrepeat_penalty 1.1"]
    MOD -- All Models --> Q["Run all 4 models\nasyncio.gather parallel\nLive 4-column table UI\n0.5s polling update"]

    Q --> QS["Specificity score per model\nCount model numbers in output\nStar highest-scoring model"]
    QS --> RESP

    N & NG & P1 & P2 --> RESP

    RESP --> CL{"is_clarification check\nIs response a brand question?"}
    CL -- Yes --> NOSRC["Do NOT append sources block\nAvoid polluting clarification turns"]
    CL -- No --> SRC["Append Retrieval Sources block\nif show_sources ON\nAttach wiki page as side element"]
    NOSRC & SRC --> HIST

    HIST["Append full response to history\nincluding perf stats suffix"]
    HIST --> TRIM[Trim history to 20 messages]
    TRIM --> SAVE["Save history and gate state to session\nLog query to wiki/log.md"]
    SAVE --> Z([End turn])

    style A fill:#4a90d9,color:#fff
    style Z fill:#4a90d9,color:#fff
    style E fill:#e67e22,color:#fff
    style SEC fill:#27ae60,color:#fff
    style BCS fill:#27ae60,color:#fff
    style FB fill:#c0392b,color:#fff
    style SYS fill:#7ed321,color:#fff
    style HINT fill:#8e44ad,color:#fff

    classDef improvement fill:#27ae60,color:#fff
    classDef gate fill:#e67e22,color:#fff
    class SEC,SEC1,SEC2,SEC3,SEC4,SEC5,BCS improvement
    class E,GS,GB,GC,GD,GK gate
```

---

## What Changed — Side-by-Side Summary

| Area | Original | Current |
|---|---|---|
| **Retrieval method** | Keyword matching only | Section-level semantic cosine similarity always active; keyword as fallback only |
| **Embedding model** | None | nomic-embed-text via local Ollama |
| **Vector database** | None | SQLite with 106+ embedded sections |
| **Wiki section headings** | Generic headings on all pages | Series-specific headings — 21 headings renamed across 7 files |
| **Context budget** | 3000 chars flat cut per page | 5000 tokens with intelligent section selection |
| **Brand gate** | None — LLM prompt only | Programmatic state machine: IDLE → AWAIT_BRAND → CONFIRMED |
| **Brand scoping** | All pages compete on every query | Confirmed brand narrows vector search to one brand's sections only |
| **Category filtering** | None | Fishing / hunting / wireless signals zero out off-category pages |
| **Multi-turn query bug** | User replies "Phenix" then retrieval runs on "Phenix" | Walks back history to recover original question after brand reply |
| **Brand re-asking bug** | STATE_CONFIRMED still ran detect_brand every turn | STATE_CONFIRMED branch skips detection entirely |
| **Overview demotion** | Hub pages compete equally with series pages | Hub pages capped at score 1.0 when any series page scores higher |
| **num_predict Ollama** | 1024 tokens max output | 4096 tokens max output |
| **Commands** | /reload only | /reload /reset /pages /search /read /wrong /speed |
| **Sources toggle** | Off by default | On by default; suppressed on clarification turns |
| **Dedicated wiki sections** | None | Iron Feather warranty, Trifecta comparison, M1 Bass specs added |
| **Wiki structure guide** | None | WIKI_STRUCTURE_GUIDE.md with rules for all future pages |
