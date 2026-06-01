#!/usr/bin/env python3
"""
test_phenix.py — Direct diagnostic for GSM Outdoors chat accuracy
Run: python3 test_phenix.py

Tests three things in sequence:
  1. Raw model speed (no wiki)
  2. Model + wiki content (direct injection)
  3. Context scoring (what pages app.py would select)
"""

import json, time, pathlib, urllib.request, urllib.error

OLLAMA_URL  = "http://localhost:11434"
MODEL       = "gemma4:e4b"
WIKI_DIR    = pathlib.Path.home() / "Desktop/RAG-GSM/Rag-New/wiki"
PHENIX_PAGE = WIKI_DIR / "fishing/phenix-rods.md"

SEP = "─" * 60

def ollama_generate(prompt: str, system: str = "", num_ctx: int = 4096, num_predict: int = 300) -> dict:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
        },
    }
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def test_speed():
    print(SEP)
    print("TEST 1 — Raw GPU speed (no wiki)")
    print(SEP)
    t0 = time.time()
    result = ollama_generate(
        prompt="List the months of the year.",
        num_ctx=512, num_predict=50
    )
    elapsed = time.time() - t0
    ec = result.get("eval_count", 0)
    ed = result.get("eval_duration", 1)
    tps = ec / (ed / 1e9) if ed else 0
    print(f"Response : {result.get('response','[empty]')[:80]}")
    print(f"Speed    : {tps:.1f} tok/s")
    print(f"Time     : {elapsed:.1f}s")
    status = "✅ EXCELLENT" if tps > 40 else "⚠️  SLOW — GPU may not be active"
    print(f"Status   : {status}")
    return tps


def test_with_wiki():
    print()
    print(SEP)
    print("TEST 2 — Model + Phenix wiki content")
    print(SEP)

    if not PHENIX_PAGE.exists():
        print(f"❌ File not found: {PHENIX_PAGE}")
        print("   Run ingest first or check the path.")
        return

    wiki_content = PHENIX_PAGE.read_text()
    print(f"Wiki page size : {len(wiki_content)} chars")
    print(f"Contains FX701 : {'FX701' in wiki_content}")
    print(f"Contains Elixir: {'Elixir' in wiki_content or 'elixir' in wiki_content.lower()}")
    print(f"Contains Mirage: {'Mirage' in wiki_content or 'mirage' in wiki_content.lower()}")
    print()

    # Show the first 600 chars so we can see what's actually in the file
    print("── First 600 chars of phenix-rods.md ──")
    print(wiki_content[:600])
    print("── End preview ──")
    print()

    system = (
        "You are a customer support assistant for GSM Outdoors. "
        "Answer ONLY from the wiki content provided. "
        "Give specific model numbers when they are in the content. "
        "If the answer is in the content, give it directly — do not say you don't have information."
    )

    prompt = f"""Here is the GSM Outdoors wiki page for Phenix Rods:

{wiki_content}

---
Customer question: What rod would be best for trout spoons around 2-3.5 grams for stocked trout? 
The customer is asking about Elixir or Mirage series in 7'0" length.
Give specific model numbers.
"""

    print(f"Sending {len(prompt)} chars to {MODEL}...")
    t0 = time.time()
    try:
        result = ollama_generate(prompt=prompt, system=system, num_ctx=8192, num_predict=400)
        elapsed = time.time() - t0
        response = result.get("response", "")
        ec = result.get("eval_count", 0)
        ed = result.get("eval_duration", 1)
        tps = ec / (ed / 1e9) if ed else 0

        print(f"Time     : {elapsed:.1f}s")
        print(f"Speed    : {tps:.1f} tok/s")
        print(f"Tokens   : {ec}")
        print()
        print("── RESPONSE ──")
        if response:
            print(response)
            has_models = any(m in response for m in ["FX701", "PHX-MF", "Elixir", "Mirage"])
            print()
            print(f"✅ Contains model numbers: {has_models}")
        else:
            print("❌ EMPTY RESPONSE — model generated zero tokens")
            print("   Likely cause: num_ctx too small for the prompt size")
            prompt_tokens_est = len(prompt) // 4
            print(f"   Estimated prompt tokens: ~{prompt_tokens_est}")
            print(f"   num_ctx used: 8192")
            if prompt_tokens_est > 7000:
                print("   ⚠️  Prompt exceeds context window — truncating wiki page")
                # Retry with truncated content
                print()
                print("   Retrying with first 3000 chars of wiki page...")
                short_prompt = f"""Wiki content (Phenix Rods):

{wiki_content[:3000]}

Customer: What rod for trout spoons 2-3.5g? Elixir or Mirage, 7'0" length? Give model numbers.
"""
                r2 = ollama_generate(short_prompt, system=system, num_ctx=4096, num_predict=400)
                r2_text = r2.get("response", "")
                print("── RETRY RESPONSE ──")
                print(r2_text if r2_text else "❌ Still empty")

    except Exception as e:
        print(f"❌ Error: {e}")


def test_context_scoring():
    print()
    print(SEP)
    print("TEST 3 — Context scoring (what app.py selects)")
    print(SEP)

    PAGE_KEYWORDS = {
        "fishing/phenix-rods.md":        ["phenix", "elixir", "mirage", "iron feather", "fx701", "phx", "no-hassle"],
        "fishing/dobyns-rods.md":        ["dobyns", "champion", "sierra"],
        "fishing/fishing-overview.md":   ["fishing", "rod", "trout", "bass", "spoon", "lure"],
        "hunting/feeders-and-timers.md": ["feeder", "timer", "boss buck", "wgi", "battery"],
        "wireless/connect-cellular.md":  ["connect", "cellular", "camera", "led", "sd card"],
    }

    query = "what rod would best for spoons for trout spoons around 2 3.5 grams for stocked trout Elixir or mirage in the 7 0 length"
    print(f"Query: {query[:80]}...")
    print()

    scores = {}
    for page, keywords in PAGE_KEYWORDS.items():
        score = 0
        hits  = []
        for kw in keywords:
            if kw.lower() in query.lower():
                score += 3
                hits.append(kw)
        scores[page] = (score, hits)

    print("Page scores:")
    for page, (score, hits) in sorted(scores.items(), key=lambda x: -x[1][0]):
        mark = "✅" if score > 0 else "  "
        print(f"  {mark} {score:3d}  {page}  {hits}")

    print()
    top = [(p, s, h) for p, (s, h) in scores.items() if s > 0]
    top.sort(key=lambda x: -x[1])
    if any(p == "fishing/phenix-rods.md" for p, _, _ in top):
        print("✅ phenix-rods.md WOULD be selected by app.py")
    else:
        print("❌ phenix-rods.md would NOT be selected — keyword mismatch")


def check_wiki_file():
    print()
    print(SEP)
    print("DIAGNOSTIC — Wiki file check")
    print(SEP)
    if not PHENIX_PAGE.exists():
        print(f"❌ File does not exist: {PHENIX_PAGE}")
        return False

    content = PHENIX_PAGE.read_text()
    fill_count = content.count("FILL")
    model_count = sum(content.count(m) for m in ["FX701", "PHX-MF", "S711"])

    print(f"File         : {PHENIX_PAGE}")
    print(f"Size         : {len(content)} chars / {len(content.splitlines())} lines")
    print(f"FILL-IN count: {fill_count}  {'⚠️  still has placeholders' if fill_count > 0 else '✅ clean'}")
    print(f"Model numbers: {model_count} found  {'✅' if model_count > 0 else '❌ none'}")
    return model_count > 0


if __name__ == "__main__":
    print()
    print("GSM Outdoors — Chat Accuracy Diagnostic")
    print(SEP)
    print(f"Model   : {MODEL}")
    print(f"Ollama  : {OLLAMA_URL}")
    print(f"Wiki dir: {WIKI_DIR}")
    print()

    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3)
    except Exception:
        print("❌ Ollama not reachable at localhost:11434")
        print("   Run: ollama serve")
        exit(1)

    ok = check_wiki_file()
    tps = test_speed()
    test_with_wiki()
    test_context_scoring()

    print()
    print(SEP)
    print("SUMMARY")
    print(SEP)
    print(f"GPU speed    : {tps:.0f} tok/s {'✅' if tps > 40 else '❌'}")
    print(f"Wiki content : {'✅ model numbers present' if ok else '❌ missing — paste content into Wiki Studio and save'}")
    print()
