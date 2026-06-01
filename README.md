# GSM Outdoors — Customer Support Wiki

Karpathy LLM-Wiki pattern applied to GSM Outdoors customer support documents.
Brands: Phenix Rods, Dobyns, Bucca, Bonehead Tackle, Avian-X, WGI/Boss Buck,
SOG Knives, Muddy, Hawk, Bloodsport, Walker's, Connect/MTRX/Stealth Cam.

## How It Works

1. Raw documents (PDFs, DOCX, XLSX, PPTX) live in `raw/` — never modified
2. The LLM reads each document and writes/updates wiki pages in `wiki/`
3. Wiki pages are plain markdown — brand pages, procedure pages, parts lists
4. Chat UI reads `index.md` to find relevant pages, then answers from the wiki
5. No vector database. No embeddings. Just markdown + a local LLM.

## Setup

```bash
# 1. Install Ollama and pull the model
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull llama3.2

# 2. Python environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Link your docs folder
ln -s "/Users/venkatanathandwarakanathan/Desktop/RAG-GSM/docs/GT - CS HUNTING & FISHING" raw/
# OR copy it:
# cp -r "/Users/venkatanathandwarakanathan/Desktop/RAG-GSM/docs" raw/
```

## Usage

### Step 1 — Ingest all documents (first time, ~20–40 min)
```bash
python scripts/ingest.py --all
```

### Step 2 — Start the chat UI
```bash
chainlit run chat/app.py --port 8000
# Open http://localhost:8000
```

### Ingest a single new document
```bash
python scripts/ingest.py --file "raw/CS FISHING/Rods - Phenix/Phenix Q&A.docx"
```

### Ingest all docs in one brand folder
```bash
python scripts/ingest.py --folder "raw/CS FISHING/Rods - Phenix"
```

### Weekly lint check
```bash
python scripts/ingest.py --lint
```

### Rebuild index after manual wiki edits
```bash
python scripts/ingest.py --reindex
```

## Project Structure

```
gsm_outdoors_wiki/
├── SCHEMA.md              ← LLM rulebook (edit this to tune behavior)
├── requirements.txt
├── raw/                   ← symlink to your docs folder
├── wiki/
│   ├── index.md           ← auto-generated catalog (do not edit manually)
│   ├── log.md             ← append-only operation history
│   ├── overview.md
│   ├── agent-operations.md
│   ├── fishing/
│   │   ├── fishing-overview.md
│   │   ├── phenix-rods.md
│   │   ├── dobyns-rods.md
│   │   ├── bucca-brand.md
│   │   ├── bonehead-tackle.md
│   │   └── dealer-inquiry.md
│   ├── hunting/
│   │   ├── feeders-and-timers.md
│   │   ├── avian-x.md
│   │   ├── tree-stands-blinds.md
│   │   ├── replacement-parts.md
│   │   ├── sog-knives.md
│   │   ├── bloodsport.md
│   │   ├── product-comparisons.md
│   │   └── procedures.md
│   └── wireless/
│       ├── wireless-overview.md
│       ├── connect-cellular.md
│       ├── muddy-mtrx.md
│       └── stealth-cam.md
├── scripts/
│   └── ingest.py          ← ingest pipeline
└── chat/
    └── app.py             ← Chainlit UI
```

## Chat Commands

| Command | What it does |
|---|---|
| `/brands` | List all brands covered |
| `/pages` | Show all wiki pages |
| `/read fishing/phenix-rods` | Read a specific wiki page |
| `/file` | Save last answer as a new wiki page |
| `/wrong` | Flag last answer as incorrect |
| `/ingest` | Re-ingest all documents |
| `/lint` | Run wiki health check |
