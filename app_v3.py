"""
app_v3.py — GSM Outdoors Customer Support Chat
─────────────────────────────────────────────
Merges app.py & app_v2.py capabilities with dynamic model routing.
Features:
  1. Chainlit Select widget to choose API backend
  2. Keyword-filtered context via wiki_retrieval for low latency
  3. "All (Simultaneous Models)" feature via asyncio.gather
  4. Consistent output tokens per second rating
"""

import asyncio
import json
import os
import sys
import time
import shutil
from datetime import date
from pathlib import Path

import httpx
import chainlit as cl
from chainlit.input_widget import Select, Switch
from loguru import logger
from dotenv import load_dotenv

from wiki_retrieval import SKIP_PAGES as _SKIP_PAGES, build_wiki_context as _wr_build_wiki_context
import brand_gate as _brand_gate
import wismo_gate as _wismo_gate
import crm_client as _crm_client

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent
WIKI_DIR    = BASE_DIR / "wiki"
SCHEMA_PATH = BASE_DIR / "SCHEMA.md"
INDEX_PATH  = WIKI_DIR / "index.md"
LOG_PATH    = WIKI_DIR / "log.md"

OLLAMA_URL = "http://localhost:11434"

# ── Context settings ──────────────────────────────────────────────────────────
NUM_CTX         = 8192    
# MAX_WIKI_TOKENS = 3000
# MAX_WIKI_TOKENS = 5000  # old
# MAX_WIKI_TOKENS = 6000  # old — too tight for 8 pages
MAX_WIKI_TOKENS = 8000
# MAX_PAGES_SENT  = 4   # old — tier-fees page ranked #6, got cut off behind product pricing sections
# MAX_PAGES_SENT  = 6   # old — tier-fees page ranked #8, still cut off for cheapest-replacement queries
MAX_PAGES_SENT  = 8
MAX_HISTORY     = 20      

# ── Brand → wiki path prefix (scopes vector search to one brand's sections) ──
# Key   = brand name as it appears in brand_gate results (gate["brands"][0])
# Value = leading path prefix of that brand's wiki files
_BRAND_WIKI_PREFIX: dict[str, str] = {
    # fishing
    "Phenix Rods":               "fishing/phenix-rods",
    "Dobyns Rods":               "fishing/dobyns-rods",
    "Bonehead Tackle":           "fishing/bonehead-tackle",
    "Bucca Brand":               "fishing/bucca-brand",
    "Northland Tackle":          "fishing/northland-tackle",
    # hunting — multi-brand pages share a prefix, that's intentional
    "WGI / Wildgame Innovations":"hunting/feeders-and-timers",
    "Boss Buck":                 "hunting/feeders-and-timers",
    "Avian-X":                   "hunting/avian-x",
    "SOG Knives":                "hunting/sog-knives",
    "Muddy":                     "hunting/replacement-parts",
    "Hawk":                      "hunting/replacement-parts",
    "Walker's Ear Protection":   "hunting/walkers",
    "Bloodsport":                "hunting/replacement-parts",
    # wireless
    "Connect Cellular":          "wireless/connect-cellular",
    "Muddy MTRX":                "wireless/muddy-mtrx",
    "Stealth Cam":               "wireless/stealth-cam",
}

# ─────────────────────────────────────────────────────────────────────────────
# Wiki cache
# ─────────────────────────────────────────────────────────────────────────────
_wiki_cache:  dict[str, str] = {}
_index_text:  str = ""
_schema_text: str = ""

# Registry: rel_path → [(alt_text, image_url), ...]
# Built from any ![alt](url) found in wiki markdown tables.
_wiki_images: dict[str, list[tuple[str, str]]] = {}

import re as _re

def _build_image_registry() -> None:
    """Scan all loaded wiki pages for markdown images and build _wiki_images."""
    global _wiki_images
    _wiki_images = {}
    _img_pat = _re.compile(r'!\[([^\]]*)\]\((https?://[^)]+)\)')
    for rel, content in _wiki_cache.items():
        matches = _img_pat.findall(content)
        if matches:
            _wiki_images[rel] = [(alt, url) for alt, url in matches]

def _images_for_ranked(ranked: list, max_images: int = 4) -> list:
    """Return cl.Image elements for product images found in the ranked wiki pages.
    Deduplicates by URL and caps at max_images to avoid flooding the chat.
    """
    seen_urls: set[str] = set()
    result = []
    for rel_path, _score in ranked:
        for alt, url in _wiki_images.get(rel_path, []):
            if url not in seen_urls:
                seen_urls.add(url)
                result.append(cl.Image(url=url, name=alt or "Product", display="inline"))
                if len(result) >= max_images:
                    return result
    return result

def _load_wiki() -> None:
    global _wiki_cache, _index_text, _schema_text
    _schema_text = SCHEMA_PATH.read_text() if SCHEMA_PATH.exists() else ""
    _index_text  = INDEX_PATH.read_text()  if INDEX_PATH.exists() else \
                   "Wiki not built yet. Run: python ingest_customer.py --all"
    _wiki_cache.clear()
    for page in WIKI_DIR.rglob("*.md"):
        rel = str(page.relative_to(WIKI_DIR))
        _wiki_cache[rel] = page.read_text()
    _build_image_registry()
        
    # Sync wiki to public directory so natively linked source documents can be served
    public_wiki = BASE_DIR / "public" / "wiki"
    if WIKI_DIR.exists():
        shutil.copytree(WIKI_DIR, public_wiki, dirs_exist_ok=True)
        
    logger.info("Wiki loaded: {} pages", len(_wiki_cache))

def _reload_wiki() -> None:
    _load_wiki()

def _page_count() -> int:
    return len(_wiki_cache)

# ─────────────────────────────────────────────────────────────────────────────
# Context builder
# ─────────────────────────────────────────────────────────────────────────────
def _build_wiki_context(query: str = "", history_text: str = "",
                        brand_hint: str | None = None) -> tuple:
    return _wr_build_wiki_context(
        query,
        history_text,
        _wiki_cache,
        MAX_PAGES_SENT,
        MAX_WIKI_TOKENS,
        _index_text,
        brand_hint,
    )

# ─────────────────────────────────────────────────────────────────────────────
# System Prompt
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a friendly, knowledgeable customer support assistant for GSM Outdoors.
GSM Outdoors sells hunting and fishing products across multiple brands.

YOUR PERSONALITY:
- Warm and conversational — like a helpful store assistant, not a robot
- Ask natural clarifying questions when needed
- Short sentences, plain language

ACCURACY RULES — non-negotiable:
1. Answer ONLY based on the provided wiki content. You may carefully reason about the content, but NEVER use outside knowledge or hallucinate facts not present in the wiki.
2. If a procedure exists in the wiki (warranty steps, cut-and-mail instructions,
   mailing address, form name, payment amount) — give THOSE EXACT STEPS verbatim.
   Never substitute "contact customer service" for a real procedure.
3. If a specific model number, part number, address, or price is in the wiki — state it exactly.
4. If the wiki does not contain the answer, say honestly:
   "I don't have that specific information — please call our support team and
   they'll be able to help you right away."
4a. HARD RULE — never invent specific facts: If a mailing address, phone number, email address, price, fee, form name, or part number is NOT quoted verbatim in the wiki content provided to you, do NOT write it. Say instead: "I don't have that detail in my notes — please call our support team and they can confirm it for you." This applies even if you believe you know the answer from other sources.
4b. BRAND RULE — never mix brands: Once a brand is confirmed in the conversation, only reference products, procedures, and details for THAT brand. Never answer a question about Brand A by citing information from Brand B, even if both are GSM Outdoors brands.
4d. LIST COMPLETENESS — When the wiki lists multiple items in a tier, category, or series (e.g., "Tier-1: Crankbait XG, Feather, Maxim"), name ALL of them. Never drop items from a list based on prior knowledge or assumptions about which seem relevant. If the wiki says Feather is Tier-1, list Feather as Tier-1 — do not substitute your own judgment about product positioning.
4c. PRODUCT LINE COMPLETENESS — When a brand has multiple product lines (e.g., premium Carbon Fiber vs E-Series) and the customer asks a generic question that does not specify which line (e.g., "your rods", "my rod", "a replacement"), address EACH product line separately in your answer. If information for a particular line is missing from your notes, say so explicitly rather than omitting that line entirely (e.g., "For the E-Series, I don't have replacement fee details in my notes — please contact support to confirm.").

CLARIFICATION RULES:
5. GSM Outdoors carries MULTIPLE brands in the same product category:
   - Fishing rods / lures / swimbaits: Phenix Rods, Dobyns Rods, Bucca Brand, Bonehead Tackle
   - Feeders: WGI (Wildgame), Boss Buck, TH-series
   - Wireless cameras: Connect Cellular, Muddy MTRX, Stealth Cam STC-DS4KTM
   - Ear protection: Walker's
6. If a customer mentions a product (e.g., "rod tip", "feeder", "casting reel") WITHOUT specifying the brand, YOU MUST ALWAYS politely ask what brand they are referring to BEFORE saying you don't have the information. Give them a numbered list of choices using ONLY the brands listed in rule 5 (e.g., "1. Phenix Rods, 2. Dobyns Rods, 3. Bucca Brand"). Do NOT invent or add brands not listed here.
7. If the model name or series IS in the question, answer directly — do NOT ask for clarification.
8. Only ask ONE clarifying question at a time.

WARRANTY RULES:
9. For ANY warranty or replacement question, ALWAYS include:
   - The procedure steps
   - The mailing address
   - The replacement fee (tier fee + return shipping)
   If the brand has multiple product lines (e.g., Carbon Fiber and E-Series), address the fee for EACH line separately. Never answer a warranty fee question without covering all applicable lines. If fee info for one line is not in your notes, say so explicitly rather than omitting it.
   When COMPARING warranties between product lines, always mention BOTH the duration difference AND any fee differences. A warranty comparison is incomplete without covering fees.

CONVERSATION RULES:
10. Once brand/model is confirmed, use it for the rest of the conversation.
11. Remember everything said earlier — don't ask for information already given.
12. After resolving an issue, ask "Is there anything else I can help you with?"
13. Keep answers focused — give the key info first, offer more detail if needed.
"""

def _build_system_prompt() -> str:
    """Build the system prompt, appending SCHEMA.md wiki conventions if available."""
    if _schema_text:
        return (
            SYSTEM_PROMPT
            + "\n\n---\n## WIKI STRUCTURE REFERENCE (from SCHEMA.md)\n"
            + _schema_text[:2000]
            + "\n---\n"
        )
    return SYSTEM_PROMPT

def _is_clarification(text: str) -> bool:
    """Determine if a response is a clarification question asking for brand/model."""
    lower_text = text.lower()
    # Strip common closing questions to avoid false positives
    cleaned = lower_text.replace("is there anything else i can help you with?", "")
    return "?" in cleaned and ("which brand" in cleaned or "what brand" in cleaned or "specify the brand" in cleaned or "options" in cleaned and "1." in cleaned)


# ─────────────────────────────────────────────────────────────────────────────
# Streaming Logic
# ─────────────────────────────────────────────────────────────────────────────
async def _run_model_stream(llm_model: str, is_openai_api: bool, is_groq_api: bool, messages: list, out_msg: cl.Message, suffix_str: str = "", elements: list = None) -> str:
    start_time = time.time()
    full_response = ""
    tokens_generated = 0
    tps = 0.0
    in_tokens = 0
    out_tokens = 0

    try:
        if is_groq_api:
            from groq import AsyncGroq
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                err = "\n\n❌ **Missing GROQ_API_KEY**: Please restart the app with your key loaded in the terminal."
                await out_msg.stream_token(err)
                return err

            client = AsyncGroq(api_key=api_key)
            completion = await client.chat.completions.create(
                model=llm_model,
                messages=messages,
                temperature=1,
                max_completion_tokens=8192,
                top_p=1,
                reasoning_effort="medium",
                stream=True,
                stop=None
            )
            
            async for chunk in completion:
                token = chunk.choices[0].delta.content or ""
                if token:
                    tokens_generated += 1
                    await out_msg.stream_token(token)
                    full_response += token

            elapsed = time.time() - start_time
            if not out_tokens: out_tokens = tokens_generated
            if elapsed > 0 and out_tokens > 0:
                tps = out_tokens / elapsed
            logger.info("Speed: {:.1f} tok/s | tokens: {} ({})", tps, out_tokens, llm_model)

        elif is_openai_api:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                err = "\n\n❌ **Missing OPENAI_API_KEY**: Please restart the app with your key loaded in the terminal."
                await out_msg.stream_token(err)
                return err

            async with httpx.AsyncClient(timeout=180) as client:
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model":    llm_model,
                        "messages": messages,
                        "stream":   True,
                        "stream_options": {"include_usage": True},
                        "temperature": 0.15
                        
                        ,
                    },
                ) as resp:
                    resp.raise_for_status()
                    async for raw_line in resp.aiter_lines():
                        if not raw_line or raw_line == "data: [DONE]":
                            continue
                        if raw_line.startswith("data: "):
                            try:
                                data  = json.loads(raw_line[6:])
                                usage = data.get("usage")
                                if usage:
                                    in_tokens = usage.get("prompt_tokens", in_tokens)
                                    out_tokens = usage.get("completion_tokens", tokens_generated)
                                choices = data.get("choices", [])
                                if not choices: continue
                                token = choices[0].get("delta", {}).get("content", "")
                                if token:
                                    tokens_generated += 1
                                    await out_msg.stream_token(token)
                                    full_response += token
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue

            elapsed = time.time() - start_time
            if not out_tokens: out_tokens = tokens_generated
            if elapsed > 0 and out_tokens > 0:
                tps = out_tokens / elapsed
            logger.info("Speed: {:.1f} tok/s | tokens: {} ({})", tps, out_tokens, llm_model)

        else:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model":   llm_model,
                        "messages": messages,
                        "stream":  True,
                        "options": {
                            "temperature": 0.15,
                            "num_predict": 4096,
                            "num_ctx":     NUM_CTX,
                            "repeat_penalty": 1.1,
                        },
                    },
                ) as resp:
                    resp.raise_for_status()
                    async for raw_line in resp.aiter_lines():
                        if not raw_line:
                            continue
                        try:
                            data  = json.loads(raw_line)
                            token = data.get("message", {}).get("content", "")
                            if token:
                                tokens_generated += 1
                                await out_msg.stream_token(token)
                                full_response += token
                            if data.get("done"):
                                out_tokens = data.get("eval_count", tokens_generated)
                                in_tokens = data.get("prompt_eval_count", 0)
                                eval_duration = data.get("eval_duration", 1)
                                if out_tokens and eval_duration:
                                    tps = out_tokens / (eval_duration / 1e9)
                                    logger.info("Speed: {:.1f} tok/s | tokens: {} ({})", tps, out_tokens, llm_model)
                        except json.JSONDecodeError:
                            continue

    except httpx.ConnectError:
        err = f"\n\n❌ Cannot reach API server for {llm_model}. Ensure Ollama/network is available."
        await out_msg.stream_token(err)
        full_response += err
    except asyncio.CancelledError:
        raise
    except GeneratorExit:
        raise
    except Exception as e:
        err = f"\n\n❌ Error ({llm_model}): {e}"
        try:
            await out_msg.stream_token(err)
        except Exception:
            pass
        full_response += err

    elapsed = time.time() - start_time
    total_cost = 0.0
    if not out_tokens: out_tokens = tokens_generated
    
    if is_openai_api:
        if llm_model == "gpt-4o":
            total_cost = (in_tokens * 0.000005) + (out_tokens * 0.000015)
        elif llm_model == "gpt-4o-mini":
            total_cost = (in_tokens * 0.00000015) + (out_tokens * 0.0000006)
        cost_str = f"USD {total_cost:.5f}"
    else:
        cost_str = "USD 0"

    time_str = f"\n\n*(Model: {llm_model} | Tokens: {in_tokens} In, {out_tokens} Out | Cost: {cost_str} | Speed: {tps:.1f} tok/s | Time: {elapsed:.2f}s)*"
    try:
        await out_msg.stream_token(time_str)
        
        lower_resp = full_response.lower()
        is_clarification = _is_clarification(full_response)
        
        if suffix_str and not is_clarification:
            if elements:
                out_msg.elements = elements
            await out_msg.stream_token(suffix_str)
            
    except Exception:
        pass

    return full_response

# ─────────────────────────────────────────────────────────────────────────────
# Log helper
# ─────────────────────────────────────────────────────────────────────────────
def _append_log(entry: str) -> None:
    today = date.today().isoformat()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text("# GSM Outdoors CS Wiki — Log\n\n")
    with open(LOG_PATH, "a") as f:
        f.write(f"\n## [{today}] {entry}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Command handler
# ─────────────────────────────────────────────────────────────────────────────
async def _handle_command(text: str) -> None:
    parts = text.strip().split(None, 1)
    cmd   = parts[0].lower()
    arg   = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/reload":
        await cl.make_async(_reload_wiki)()
        await cl.Message(content=f"✅ Wiki reloaded — **{_page_count()} pages** in cache.").send()

    elif cmd == "/reset":
        cl.user_session.set("history", [])
        cl.user_session.set("gate", _brand_gate.get_session_defaults())
        await cl.Message(content="✅ Conversation history cleared. Starting fresh.").send()

    elif cmd == "/pages":
        if not _wiki_cache:
            await cl.Message(content="⚠️ Wiki cache is empty. Run `/reload`.").send()
            return
        lines = [f"**{_page_count()} loaded wiki pages:**\n"]
        for rel in sorted(_wiki_cache.keys()):
            if rel.replace("\\", "/") in _SKIP_PAGES:
                continue
            lines.append(f"- `{rel}`")
        await cl.Message(content="\n".join(lines)).send()

    elif cmd == "/search":
        if not arg:
            await cl.Message(content="Usage: `/search <query>`\nExample: `/search lightest steelhead rod`").send()
            return
        from vector_store import get_top_sections, is_ready as _vs_ready
        if not _vs_ready():
            await cl.Message(content="⚠️ Vector store not built. Run:\n```\npython vector_store.py --build\n```").send()
            return
        sections = get_top_sections(arg, top_n=8, min_score=0.50)
        if not sections:
            await cl.Message(content=f"No sections found above 0.50 for: `{arg}`").send()
            return
        lines = [f"**Section search:** `{arg}`\n"]
        for sec in sections:
            lines.append(
                f"- `{sec['score']:.4f}` — `{sec['rel_path']}` § **{sec['section_heading']}**"
            )
        await cl.Message(content="\n".join(lines)).send()

    elif cmd == "/read":
        if not arg:
            await cl.Message(content="Usage: `/read <page>`\nExample: `/read fishing/phenix-rods-warranty`").send()
            return
        rel = arg if arg.endswith(".md") else arg + ".md"
        content = _wiki_cache.get(rel) or _wiki_cache.get(rel.replace("/", "\\"))
        if not content:
            await cl.Message(
                content=f"⚠️ Page not found: `{rel}`\nUse `/pages` to list available pages."
            ).send()
            return
        element = cl.Text(name=rel, content=content, display="side")
        await cl.Message(
            content=f"📄 `{rel}` — {len(content):,} chars",
            elements=[element],
        ).send()

    elif cmd == "/wrong":
        history = cl.user_session.get("history") or []
        last_q = next((m["content"] for m in reversed(history) if m["role"] == "user"), None)
        if not last_q:
            await cl.Message(content="⚠️ No conversation to flag yet.").send()
            return
        note = arg or "(no note provided)"
        _append_log(f"WRONG_ANSWER | note: {note} | Q: {last_q[:120]}")
        await cl.Message(
            content=(
                f"⚠️ **Flagged as incorrect** — logged for review.\n\n"
                f"**Question:** {last_q[:150]}\n"
                f"**Note:** {note}"
            )
        ).send()

    elif cmd == "/speed":
        history = cl.user_session.get("history") or []
        last_a = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), None)
        if last_a:
            import re as _re
            m = _re.search(r"\*\(Model:.*?\)\*", last_a)
            if m:
                await cl.Message(content=f"**Last response stats:**\n{m.group(0)}").send()
                return
        await cl.Message(content="No speed stats yet — send a message first.").send()

    elif cmd == "/lint":
        await cl.Message(content="🔍 Running wiki lint check...").send()
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("ingest_customer", BASE_DIR / "ingest_customer.py")
            _ic   = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_ic)
            await cl.make_async(_ic.lint_wiki)()
            report_path = WIKI_DIR / "lint-report.md"
            if report_path.exists():
                report_text = report_path.read_text(encoding="utf-8")
                issue_count = report_text.count("❌") + report_text.count("⚠️")
                summary = (
                    f"✅ **Wiki lint complete — no issues found.**"
                    if issue_count == 0
                    else f"⚠️ **Wiki lint complete — {issue_count} issue(s) found.** See report →"
                )
                element = cl.Text(name="lint-report.md", content=report_text, display="side")
                await cl.Message(content=summary, elements=[element]).send()
            else:
                await cl.Message(content="✅ Lint complete — no report generated (no issues).").send()
        except Exception as _e:
            await cl.Message(content=f"❌ Lint failed: {_e}").send()

    else:
        await cl.Message(
            content=(
                "**Available commands:**\n\n"
                "- `/reload` — reload wiki pages from disk into cache\n"
                "- `/reset` — clear conversation history\n"
                "- `/pages` — list all loaded wiki pages\n"
                "- `/search <query>` — test section-level vector retrieval\n"
                "- `/read <page>` — display a wiki page in the side panel\n"
                "- `/wrong [note]` — flag the last answer as incorrect\n"
                "- `/speed` — show last response speed stats\n"
                "- `/lint` — run wiki health check and show issues"
            )
        ).send()


# ─────────────────────────────────────────────────────────────────────────────
# Chainlit handlers
# ─────────────────────────────────────────────────────────────────────────────
@cl.on_chat_start
async def on_start():
    await cl.make_async(_load_wiki)()
    # Reranker is pre-loaded at vector_store import time (main thread, before
    # Chainlit event loop) — no async pre-load needed here.

    if not os.environ.get("OPENAI_API_KEY"):
        await cl.Message(
            content="⚠️ **Missing OPENAI_API_KEY**\n\nThe app supports cloud endpoints, but no API key was found. Please ensure it is loaded if choosing OpenAI models."
        ).send()

    cl.user_session.set("history", [])
    cl.user_session.set("gate", _brand_gate.get_session_defaults())
    cl.user_session.set("wismo", _wismo_gate.get_session_defaults())

    settings = await cl.ChatSettings(
        [
            Select(
                id="model",
                label="Select Chat Model",
                values=[
                    "OpenAI (Default - gpt-4o)",        # upgraded from gpt-4o-mini — better instruction following
                    # "OpenAI (Default - gpt-4o-mini)", # previous default
                    "Microsoft Copilot eqv. (gpt-4o)",
                    "Groq (openai/gpt-oss-120b)",
                    "Local Ollama (gemma4:e4b)",
                    "Local Ollama (qwen3.5:9b)",
                    "All (Simultaneous Models)"
                ],
                initial_index=0,
            ),
            Switch(
                id="show_sources",
                label="Show Sources & Match Score",
                initial=True,
            )
        ]
    ).send()
    cl.user_session.set("settings", settings)

    if _page_count() == 0:
        await cl.Message(
            content=(
                "⚠️ **Wiki is empty.**\n\n"
                "Run this first:\n"
                "```bash\npython ingest_customer.py --all\n```"
            )
        ).send()
        return

    await cl.Message(
        content=(
            f"Hi! Welcome to **GSM Outdoors Support v3**. 👋\n\n"
            f"Use the **Settings Panel** ⚙️ below/beside the chat to select your preferred model.\n\n"
            f"What can I help you with today?"
        )
    ).send()

@cl.on_settings_update
async def setup_agent(settings):
    cl.user_session.set("settings", settings)
    await cl.Message(content=f"✅ Model changed to **{settings['model']}**").send()

@cl.on_message
async def on_message(message: cl.Message):
    user_text = message.content.strip()
    if not user_text:
        return

    # Route commands
    if user_text.startswith("/"):
        await _handle_command(user_text)
        return

    # ── WISMO intercept (runs before RAG pipeline) ────────────────────────────
    _wismo_session = cl.user_session.get("wismo") or _wismo_gate.get_session_defaults()
    _ws = _wismo_gate.from_session(_wismo_session)

    # Detect fresh WISMO intent (only when not already in a WISMO flow)
    if _ws.state == _wismo_gate.STATE_IDLE and _wismo_gate.detect_wismo(user_text):
        if not _crm_client.is_configured():
            # D365 not configured — fall through to RAG (no CRM available)
            pass
        else:
            _ws.state = _wismo_gate.STATE_AWAIT_PII
            cl.user_session.set("wismo", _wismo_gate.to_session(_ws))
            await cl.Message(content=_wismo_gate.ask_for_pii()).send()
            return

    # Handle active WISMO flow (PII collection + CRM lookup)
    elif _ws.state in (_wismo_gate.STATE_AWAIT_PII, _wismo_gate.STATE_AWAIT_RETRY):
        _email = _wismo_gate.extract_email(user_text)
        _phone = _wismo_gate.extract_phone(user_text)
        _name  = _wismo_gate.extract_name(user_text)

        if not any([_email, _phone, _name]):
            # User typed something but no identifier found — retry up to limit
            _ws.pii_attempts += 1
            if _ws.pii_attempts >= _wismo_gate.MAX_PII_ATTEMPTS:
                _ws.state = _wismo_gate.STATE_ESCALATED
                cl.user_session.set("wismo", _wismo_gate.to_session(_ws))
                await cl.Message(content=_wismo_gate.escalation_message()).send()
            else:
                _ws.state = _wismo_gate.STATE_AWAIT_RETRY
                cl.user_session.set("wismo", _wismo_gate.to_session(_ws))
                await cl.Message(content=_wismo_gate.ask_for_pii_retry()).send()
            return

        # Identifier extracted — call CRM (sync wrapped for async handler)
        _result = await cl.make_async(_crm_client.lookup_customer_orders)(
            email=_email, phone=_phone, name=_name
        )

        if _result.get("status") == "not_found":
            _ws.pii_attempts += 1
            if _ws.pii_attempts >= _wismo_gate.MAX_PII_ATTEMPTS:
                _ws.state = _wismo_gate.STATE_ESCALATED
                cl.user_session.set("wismo", _wismo_gate.to_session(_ws))
                await cl.Message(content=_wismo_gate.escalation_message()).send()
            else:
                _ws.state = _wismo_gate.STATE_AWAIT_RETRY
                cl.user_session.set("wismo", _wismo_gate.to_session(_ws))
                await cl.Message(content=_wismo_gate.ask_for_pii_retry()).send()
            return

        # All other outcomes (found, ambiguous, no_orders, api_error) — respond and close flow
        _reply = _wismo_gate.format_order_result(_result)
        if _reply:
            _ws.state = _wismo_gate.STATE_RESOLVED
            cl.user_session.set("wismo", _wismo_gate.to_session(_ws))
            await cl.Message(content=_reply).send()
            return
        # If format_order_result returned None (shouldn't happen) — fall through to RAG
    # ── End WISMO intercept ───────────────────────────────────────────────────

    history: list[dict] = cl.user_session.get("history") or []
    history.append({"role": "user", "content": user_text})

    settings = cl.user_session.get("settings")
    selected_model_str = settings.get("model", "OpenAI (Default - gpt-4o)") if settings else "OpenAI (Default - gpt-4o)"
    show_sources = settings.get("show_sources", True) if settings else True

    last_user = ""
    for msg in reversed(history):
        if msg["role"] == "user":
            last_user = msg["content"]
            break

    history_text = " ".join(
        m["content"] for m in history[-6:]
        if m["role"] in ("user", "assistant")
    )

    # ── Brand gate ────────────────────────────────────────────────────────────
    gate_session = cl.user_session.get("gate") or _brand_gate.get_session_defaults()
    gate_state   = gate_session.get("gate_state", _brand_gate.STATE_IDLE)
    brand_hint   = None

    if gate_state == _brand_gate.STATE_AWAIT_BRAND:
        # User is replying to "which brand?" — resolve and continue
        brand = _brand_gate.resolve_brand_answer(
            user_text, gate_session.get("confirmed_category", "")
        )
        if brand:
            gate_session["confirmed_brand"]       = brand
            gate_session["gate_state"]            = _brand_gate.STATE_CONFIRMED
            gate_session["awaiting_clarification"] = False
            cl.user_session.set("gate", gate_session)
            # Recover original query: user_text here is the brand reply ("Phenix"),
            # not the actual question. Walk back past ALL clarification replies
            # (single-digit option numbers or short labels) to find the real query.
            # OLD (single-skip — breaks when category gate also fired before brand gate):
            # _skip = True
            # for _msg in reversed(history):
            #     if _msg["role"] == "user":
            #         if _skip:
            #             _skip = False
            #             continue
            #         last_user = _msg["content"]
            #         break
            # NEW: skip any reply that looks like a clarification answer
            def _is_clarify_reply(t: str) -> bool:
                t = t.strip()
                if t.isdigit():          # single option number: "1", "2", "4"
                    return True
                return len(t.split()) <= 2 and len(t) <= 20  # short label: "Phenix", "Bucca Brand"

            for _msg in reversed(history):
                if _msg["role"] == "user":
                    if _is_clarify_reply(_msg["content"]):
                        continue
                    last_user = _msg["content"]
                    break
            # Fall through — proceed to retrieval using the original query
        else:
            # Could not resolve brand — ask again
            cat = gate_session.get("confirmed_category", "")
            await cl.Message(
                content=_brand_gate.ask_brand_after_product(cat)
            ).send()
            cl.user_session.set("history", history)
            return

    elif gate_state == _brand_gate.STATE_AWAIT_CATEGORY:
        # User is replying to "which category?" — resolve then ask for brand
        category = _brand_gate.resolve_category_answer(user_text)
        if category:
            gate_session["confirmed_category"]    = category
            gate_session["gate_state"]            = _brand_gate.STATE_AWAIT_BRAND
            gate_session["awaiting_clarification"] = True
            cl.user_session.set("gate", gate_session)
            cl.user_session.set("history", history)
            await cl.Message(
                content=_brand_gate.ask_brand_after_product(category)
            ).send()
            return
        else:
            # Could not resolve category — ask again
            cl.user_session.set("history", history)
            await cl.Message(
                content=_brand_gate._ask_category_and_product()
            ).send()
            return

    elif gate_state == _brand_gate.STATE_CONFIRMED:
        # Brand already confirmed in a previous turn — skip re-detection entirely.
        # Using confirmed_brand from session directly (read below).
        pass

    else:
        # STATE_IDLE — run full brand detection on this query
        result = _brand_gate.detect_brand(user_text, history, gate_session)
        if result["needs_clarification"]:
            gate_session["gate_state"]            = result["next_state"]
            gate_session["confirmed_category"]    = result.get("category")
            gate_session["awaiting_clarification"] = True
            cl.user_session.set("gate", gate_session)
            cl.user_session.set("history", history)
            await cl.Message(content=result["question"]).send()
            return
        else:
            # Brand detected — update session
            if result["brands"]:
                gate_session["confirmed_brand"] = result["brands"][0]
            gate_session["confirmed_category"]    = result.get("category") or gate_session.get("confirmed_category")
            gate_session["gate_state"]            = result["next_state"]
            gate_session["awaiting_clarification"] = False
            cl.user_session.set("gate", gate_session)

    # Map confirmed brand → wiki path prefix for vector store scoping
    confirmed_brand = gate_session.get("confirmed_brand")
    if confirmed_brand:
        brand_hint = _BRAND_WIKI_PREFIX.get(confirmed_brand)

    # For short follow-up queries (<=5 words), prepend the last assistant response
    # so vector search has enough context to find the right page.
    # "boron program" alone doesn't match the warranty page; "...Boron Legacy Program... boron program" does.
    # _OLD: wiki_context, ranked = _build_wiki_context(last_user, history_text)  # no brand scoping
    # _OLD: wiki_context, ranked = _build_wiki_context(last_user, history_text, brand_hint=brand_hint)  # no follow-up enrichment
    _retrieval_query = last_user
    if len(last_user.split()) <= 5:
        for _hm in reversed(history):
            if _hm["role"] == "assistant":
                _prior_ctx = _hm["content"][:200].replace("\n", " ")
                _retrieval_query = f"{_prior_ctx} {last_user}"
                break
    wiki_context, ranked = _build_wiki_context(_retrieval_query, history_text, brand_hint=brand_hint)

    elements = []
    confidence_str = ""
    if show_sources:
        confidence_str = "\n\n---\n**Retrieval Sources:**\n"
        if ranked:
            for rel_path, score in ranked:
                page_content = _wiki_cache.get(rel_path, "Content not found.")
                elements.append(cl.Text(name=rel_path, content=page_content, display="side"))
                confidence_str += f"- {rel_path} (Match Score: {score})\n"
        else:
            confidence_str += "- No specific wiki articles matched.\n"

    # Attach product images from any ranked wiki page that contains markdown images
    elements.extend(_images_for_ranked(ranked))

    brand_confirmation_note = ""
    if confirmed_brand:
        brand_confirmation_note = (
            f"⚠️ OVERRIDE — BRAND ALREADY CONFIRMED: This conversation is about {confirmed_brand}. "
            f"NEVER ask the customer which brand they mean — it is {confirmed_brand}. "
            f"This overrides all other instructions. Answer every question as if the customer said '{confirmed_brand}' explicitly.\n\n"
        )

    full_system = f"{brand_confirmation_note}{_build_system_prompt()}\n\n=== GSM OUTDOORS PRODUCT WIKI ===\nUse ONLY the information below to answer customer questions. If the answer is not here, say so honestly.\n\n{wiki_context}\n=== END WIKI ==="

    # Clean history before sending to LLM: remove brand gate clarification exchanges
    # (the "which brand?" assistant message + single-digit user replies) so the LLM
    # doesn't pattern-match them and regenerate a clarification question.
    # Known brand labels the customer might type in response to "which brand?" prompt
    _BRAND_REPLY_LABELS = {
        "phenix", "phenix rods", "dobyns", "dobyns rods",
        "bucca", "bucca brand", "bonehead", "bonehead tackle",
        "fishing", "freshwater", "saltwater",
    }

    def _is_gate_clarification(msg: dict) -> bool:
        content = (msg.get("content") or "").strip()
        role = msg.get("role", "")
        if role == "assistant" and "just reply with the number" in content.lower():
            return True
        if role == "user":
            if content.isdigit():   # option number: "1", "2", "4"
                return True
            # Named brand/category labels only — NOT arbitrary short strings.
            # Old broad catch-all (len<=2 words, len<=20) was filtering out legitimate
            # follow-up questions like "boron program", "rod broke", "shipping cost".
            # _OLD: if (len(content.split()) <= 2 and len(content) <= 20 and not any(...)):
            if content.lower() in _BRAND_REPLY_LABELS:
                return True
        return False

    clean_history = [m for m in history[-MAX_HISTORY:] if not _is_gate_clarification(m)]

    # When brand was just confirmed, anchor it in history with a clear synthetic exchange
    if confirmed_brand and gate_session.get("gate_state") == _brand_gate.STATE_CONFIRMED:
        # Rewrite the last user message to include brand context so LLM has no ambiguity
        for i in range(len(clean_history) - 1, -1, -1):
            if clean_history[i]["role"] == "user":
                orig = clean_history[i]["content"]
                if confirmed_brand.lower() not in orig.lower():
                    clean_history[i] = {"role": "user", "content": f"[Asking about {confirmed_brand}] {orig}"}
                break

    messages = [{"role": "system", "content": full_system}]
    messages.extend(clean_history)

    if selected_model_str == "All (Simultaneous Models)":
        models = [
            ("gpt-4o-mini", True, "OpenAI (gpt-4o-mini)"),
            ("gpt-4o", True, "Copilot (gpt-4o)"),
            ("gemma4:e4b", False, "Ollama (gemma4)"),
            ("qwen3.5:9b", False, "Ollama (qwen3.5)"),
        ]
        
        outputs = [""] * 4
        raw_outputs = [""] * 4
        metrics = ["Initializing..."] * 4
        
        master_msg = cl.Message(content="Starting models...")
        await master_msg.send()

        is_running = True

        def generate_html():
            def safe_format(t):
                if not t:
                    return ""
                # Escape pipes and replace newlines with HTML breaks to keep the markdown table row intact
                return t.replace("|", "&#124;").replace("\n", "<br>")

            table = f"| **{models[0][2]}** | **{models[1][2]}** | **{models[2][2]}** | **{models[3][2]}** |\n"
            table += "|---|---|---|---|\n"
            table += f"| {safe_format(outputs[0])} | {safe_format(outputs[1])} | {safe_format(outputs[2])} | {safe_format(outputs[3])} |\n"
            table += f"| *{safe_format(metrics[0])}* | *{safe_format(metrics[1])}* | *{safe_format(metrics[2])}* | *{safe_format(metrics[3])}* |"
            return table

        async def update_ui():
            while is_running:
                master_msg.content = generate_html()
                await master_msg.update()
                await asyncio.sleep(0.5)

        ui_task = asyncio.create_task(update_ui())

        async def run_internal(idx, llm_model, is_openai_api):
            start_time = time.time()
            tokens_generated = 0
            tps = 0.0
            in_tokens = 0
            out_tokens = 0
            metrics[idx] = "Running..."
            
            try:
                if is_openai_api:
                    api_key = os.environ.get("OPENAI_API_KEY")
                    if not api_key:
                        outputs[idx] = "❌ Missing OPENAI_API_KEY"
                        metrics[idx] = "Error"
                        return

                    async with httpx.AsyncClient(timeout=180) as client:
                        async with client.stream(
                            "POST",
                            "https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {api_key}"},
                            json={
                                "model":    llm_model,
                                "messages": messages,
                                "stream":   True,
                                "stream_options": {"include_usage": True},
                                "temperature": 0.15,
                            },
                        ) as resp:
                            resp.raise_for_status()
                            async for raw_line in resp.aiter_lines():
                                if not raw_line or raw_line == "data: [DONE]":
                                    continue
                                if raw_line.startswith("data: "):
                                    try:
                                        data  = json.loads(raw_line[6:])
                                        usage = data.get("usage")
                                        if usage:
                                            in_tokens = usage.get("prompt_tokens", in_tokens)
                                            out_tokens = usage.get("completion_tokens", tokens_generated)
                                        choices = data.get("choices", [])
                                        if not choices: continue
                                        token = choices[0].get("delta", {}).get("content", "")
                                        if token:
                                            tokens_generated += 1
                                            outputs[idx] += token
                                            raw_outputs[idx] += token
                                    except (json.JSONDecodeError, KeyError, IndexError):
                                        continue

                    elapsed = time.time() - start_time
                    if not out_tokens: out_tokens = tokens_generated
                    cost = 0.0
                    if llm_model == "gpt-4o":
                        cost = (in_tokens * 0.000005) + (out_tokens * 0.000015)
                    elif llm_model == "gpt-4o-mini":
                        cost = (in_tokens * 0.00000015) + (out_tokens * 0.0000006)
                    
                    if elapsed > 0 and out_tokens > 0:
                        tps = out_tokens / elapsed
                    metrics[idx] = f"Cost: USD {cost:.5f}<br>Tokens: {in_tokens} In, {out_tokens} Out<br>Speed: {tps:.1f} tok/s | Time: {elapsed:.2f}s"
                    
                    lower_resp = raw_outputs[idx].lower()
                    if not _is_clarification(raw_outputs[idx]):
                        outputs[idx] += confidence_str

                else:
                    async with httpx.AsyncClient(timeout=120) as client:
                        async with client.stream(
                            "POST",
                            f"{OLLAMA_URL}/api/chat",
                            json={
                                "model":   llm_model,
                                "messages": messages,
                                "stream":  True,
                                "options": {
                                    "temperature": 0.15,
                                    "num_predict": 4096,
                                    "num_ctx":     NUM_CTX,
                                    "repeat_penalty": 1.1,
                                },
                            },
                        ) as resp:
                            resp.raise_for_status()
                            async for raw_line in resp.aiter_lines():
                                if not raw_line:
                                    continue
                                try:
                                    data  = json.loads(raw_line)
                                    token = data.get("message", {}).get("content", "")
                                    if token:
                                        tokens_generated += 1
                                        outputs[idx] += token
                                        raw_outputs[idx] += token
                                    if data.get("done"):
                                        out_tokens = data.get("eval_count", tokens_generated)
                                        in_tokens = data.get("prompt_eval_count", 0)
                                        eval_duration = data.get("eval_duration", 1)
                                        if out_tokens and eval_duration:
                                            tps = out_tokens / (eval_duration / 1e9)
                                except json.JSONDecodeError:
                                    continue
                    elapsed = time.time() - start_time
                    if not out_tokens: out_tokens = tokens_generated
                    metrics[idx] = f"Cost: USD 0<br>Tokens: {in_tokens} In, {out_tokens} Out<br>Speed: {tps:.1f} tok/s | Time: {elapsed:.2f}s"
                    
                    lower_resp = raw_outputs[idx].lower()
                    if not _is_clarification(raw_outputs[idx]):
                        outputs[idx] += confidence_str

            except Exception as e:
                outputs[idx] += f"\n❌ Error: {e}"
                metrics[idx] = "Failed"

        tasks = [asyncio.create_task(run_internal(i, m_id, is_oa)) for i, (m_id, is_oa, _) in enumerate(models)]
        await asyncio.gather(*tasks)
        
        is_running = False
        ui_task.cancel()
        
        import re
        scores = []
        for i, out in enumerate(raw_outputs):
            model_numbers = re.findall(r'[A-Z]{2,}\d{3}[-\d]*|[A-Z]{3}-[A-Z]{2}-[A-Z]\d+', out)
            score = len(model_numbers)
            scores.append(score)
            metrics[i] += f"<br>Specificity Score: {score}"
            
        max_score = max(scores) if scores else 0
        if max_score > 0:
            for i, score in enumerate(scores):
                if score == max_score:
                    m_id, is_oa, m_name = models[i]
                    models[i] = (m_id, is_oa, f"⭐ {m_name}")
        
        show_elements = False
        for out in raw_outputs:
            lower_resp = out.lower()
            if out and not _is_clarification(out):
                show_elements = True
                break
                
        if show_elements and elements:
            master_msg.elements = elements
            
        master_msg.content = generate_html()
        await master_msg.update()
        
        if raw_outputs[0]:
            history.append({"role": "assistant", "content": raw_outputs[0]})

    else:
        is_openai_api = False
        is_groq_api = False
        if "gpt-oss-120b" in selected_model_str:
            llm_model = "openai/gpt-oss-120b"
            is_groq_api = True
        elif "gpt-4o-mini" in selected_model_str:
            llm_model = "gpt-4o-mini"
            is_openai_api = True
        elif "gpt-4o" in selected_model_str:
            llm_model = "gpt-4o"
            is_openai_api = True
        elif "qwen3.5" in selected_model_str:
            llm_model = "qwen3.5:9b"
        else:
            llm_model = "gemma4:e4b"
            
        out_msg = cl.Message(content="", author=selected_model_str)
        await out_msg.send()

        response = await _run_model_stream(llm_model, is_openai_api, is_groq_api, messages, out_msg, confidence_str, elements)
        await out_msg.update()

        if response:
            history.append({"role": "assistant", "content": response})

    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]

    cl.user_session.set("history", history)
    _append_log(f"query | {user_text[:80]}")
