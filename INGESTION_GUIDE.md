# GSM Wiki Studio Ingestion Guide

Welcome to the GSM Wiki Studio Ingestion Guide! This document explains how to use the ingestion pipeline (`ingest_customer.py`) to process raw internal documents (like agent instructions, Word docs, and PDFs) and transform them into clean, structured Markdown pages for the customer-facing support chatbot.

## Overview
The ingestion script reads documents from the `raw/` directory, processes them using the LLM to extract customer-facing procedures, and outputs formatted `.md` files into the `wiki/` directory. It uses a state tracking file (`.ingest_customer_state.json`) so it only processes new or modified files to save time and API costs.

---

## Preparation
Before running the ingestion pipeline:
1. Ensure your virtual environment is activated and dependencies are installed (see the Installation Guide).
2. Ensure you have your `OPENAI_API_KEY` set in the `.env` file, as the ingestion relies on OpenAI to reformat the content.
3. Place your raw source documents (e.g., `.docx`, `.txt`) into the `raw/` directory.

---

## Basic Commands

### 1. Ingest Everything (Standard Update)
This is the most common command. It scans the `raw/` directory and processes any new or updated files:
```bash
python ingest_customer.py --all
```

### 2. Force a Full Re-Ingest
If you want to ignore the state file and completely rebuild the wiki from scratch (this may take a while and consume more API credits):
```bash
python ingest_customer.py --all --force
```

---

## Targeted Ingestion Commands

If you only want to process specific updates without scanning the entire directory, you can target individual files or folders.

### Process a Single File
```bash
python ingest_customer.py --file "raw/CS FISHING/Rods - Phenix/Phenix Q&A.docx"
```

### Process an Entire Subfolder
```bash
python ingest_customer.py --folder "raw/CS HUNTING/Feeders & Timers"
```

---

## Maintenance & Health Checks

### Wiki Health Linting
The pipeline includes a built-in linter to check for missing model numbers, broken links, or formatting errors in the generated wiki files. Run this after making bulk changes to ensure high quality for the Chatbot:
```bash
python ingest_customer.py --lint
```

### Rebuild the Index
If you manually delete or edit files in the `wiki/` directory and just need to update the main `index.md` file (which the chatbot uses as a table of contents), run:
```bash
python ingest_customer.py --reindex
```

---

## Troubleshooting & Tips
- **Missing Module Errors:** Run `pip install -r requirements.txt` again to ensure doc-parsing libraries are installed.
- **Empty Wiki Warning:** If the chatbot complains about an empty wiki, you probably just need to run `python ingest_customer.py --all` to generate the documents.
- **Agent vs. Customer Docs:** Use `ingest_customer.py` for the customer-facing chat. There is a separate `ingest.py` script intended for internal agent-facing docs, and they do not conflict with each other.
