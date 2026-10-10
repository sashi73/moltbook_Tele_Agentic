"""
gemini_brain.py - the fast, cloud brain (Google Gemini) for the Telegram path.

Used for: answering Telegram questions (with Google Search for current data),
writing the Moltbook post, and reading the Moltbook maths challenge.
The background heartbeat keeps using Ollama (see brain.py).

Settings (.env)
  GEMINI_API_KEY   your key (never put it in code)
  GEMINI_MODEL     default gemini-3.1-flash-lite: the best-value model,
                   $0.25 in / $1.50 out per 1M tokens.
                   If `python gemini_brain.py` shows no sources (search unavailable
                   for this model), switch to gemini-3.8-flash ($0.75 / $3.75 until
                   31 Dec 2026, doubling from 1 Jan 2027), which lists search support.
  GEMINI_SEARCH    on/off (default on). Search grounding needs a paid (billing-
                   enabled) project: 5,000 searches/month free, then $14 per 1,000.
                   On the free tier this module notices and answers without search.

Cost tracking: every call is appended to gemini_usage.jsonl; month_summary()
returns totals and an estimated cost (Telegram /usage).
"""
import json
import os
import re
import time

import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
SEARCH_ON = os.environ.get("GEMINI_SEARCH", "on").lower() not in ("off", "0", "false", "no")
BASE = "https://generativelanguage.googleapis.com/v1beta"
USAGE_FILE = os.path.join(SCRIPT_DIR, "gemini_usage.jsonl")

# USD per 1M tokens (paid tier), for the cost estimate only. Update if Google changes prices.
PRICES = {
    "gemini-3.8-flash": (0.75, 3.75),        # doubles to (1.50, 7.50) from 1 Jan 2027
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}


def _price(model):
    pin, pout = PRICES.get(model, PRICES["gemini-3.1-flash-lite"])
    if model in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash") and time.strftime("%Y") >= "2027":
        pin, pout = pin * 2, pout * 2
    return pin, pout
SEARCH_FREE_PER_MONTH = 5000
SEARCH_PRICE_PER_1000 = 14.0

_search_unavailable = False   # set once Google says search isn't allowed on this key


class GeminiError(Exception):
    pass


def available():
    return bool(API_KEY)


# ---------------------------------------------------------------------------
# Low-level calls
# ---------------------------------------------------------------------------

def _post(url, body, timeout=60):
    try:
        r = requests.post(url, json=body, timeout=timeout,
                          headers={"x-goog-api-key": API_KEY, "Content-Type": "application/json"})
    except requests.RequestException as e:
        raise GeminiError(f"network error: {e}")
    try:
        data = r.json()
    except ValueError:
        data = {}
    return r.status_code, data, r.text


def _err_text(data, raw):
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return str(data["error"].get("message") or data["error"])
    return raw[:300]


def _parse_interactions(data):
    """Text, sources and usage from an Interactions API response."""
    texts, sources = [], []
    for step in data.get("steps") or []:
        if step.get("type") not in (None, "model_output"):
            continue
        for block in step.get("content") or []:
            if block.get("type") in (None, "text") and block.get("text"):
                texts.append(block["text"])
                for a in block.get("annotations") or []:
                    if a.get("type") == "url_citation" and a.get("url"):
                        sources.append((a.get("title") or a["url"], a["url"]))
    if not texts and isinstance(data.get("output_text"), str):
        texts.append(data["output_text"])
    u = data.get("usage") or {}
    usage = {"in": u.get("total_input_tokens", 0) or 0,
             "out": (u.get("total_output_tokens", 0) or 0) + (u.get("total_thought_tokens", 0) or 0)}
    searched = any(s.get("type") == "google_search_call" for s in data.get("steps") or [])
    return "".join(texts).strip(), sources, usage, searched


def _parse_legacy(data):
    """Text, sources and usage from the older generateContent response."""
    texts, sources = [], []
    cands = data.get("candidates") or []
    if cands:
        for part in (cands[0].get("content") or {}).get("parts") or []:
            if part.get("text"):
                texts.append(part["text"])
        gm = cands[0].get("groundingMetadata") or {}
        for ch in gm.get("groundingChunks") or []:
            w = ch.get("web") or {}
            if w.get("uri"):
                sources.append((w.get("title") or w["uri"], w["uri"]))
    u = data.get("usageMetadata") or {}
    usage = {"in": u.get("promptTokenCount", 0) or 0,
             "out": (u.get("candidatesTokenCount", 0) or 0) + (u.get("thoughtsTokenCount", 0) or 0)}
    searched = bool(cands and (cands[0].get("groundingMetadata") or {}).get("webSearchQueries"))
    return "".join(texts).strip(), sources, usage, searched


def _call_interactions(prompt, system, search, max_tokens, temperature, thinking):
    """Tries the full request, then drops optional fields if the API rejects them."""
    base = {"model": MODEL, "input": prompt, "store": False}
    if system:
        base["system_instruction"] = system
    levels = [thinking] + (["low"] if thinking == "minimal" else [])
    attempts = []
    for lvl in levels:   # if the model rejects "minimal", try "low" before dropping it
        attempts.append({"max_output_tokens": max_tokens, "temperature": temperature,
                         "thinking_level": lvl})
    for lvl in levels:
        attempts.append({"max_output_tokens": max_tokens, "thinking_level": lvl})
    attempts += [{"max_output_tokens": max_tokens}, None]
    last = ""
    for gen in attempts:
        body = dict(base)
        if gen:
            body["generation_config"] = gen
        if search:
            body["tools"] = [{"type": "google_search"}]
        status, data, raw = _post(f"{BASE}/interactions", body)
        if status == 200:
            return _parse_interactions(data)
        last = _err_text(data, raw)
        if status == 404:
            raise LookupError(last)           # endpoint/model not found: try legacy API
        if status in (401, 403):
            if search and re.search(r"search|ground|tool|billing|tier", last, re.I):
                raise PermissionError(last)   # search not allowed on this key
            raise GeminiError(f"key rejected ({status}): {last}")
        if status == 429:
            raise GeminiError(f"rate limit or quota reached: {last}")
        if status == 400 and search and re.search(r"search|ground|tool|billing|tier", last, re.I):
            raise PermissionError(last)
        if status != 400:
            raise GeminiError(f"HTTP {status}: {last}")
        # 400 for some other field: try a simpler request
    raise GeminiError(f"request rejected: {last}")


def _call_legacy(prompt, system, search, max_tokens, temperature):
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if search:
        body["tools"] = [{"google_search": {}}]
    status, data, raw = _post(f"{BASE}/models/{MODEL}:generateContent", body)
    if status == 200:
        return _parse_legacy(data)
    msg = _err_text(data, raw)
    if search and re.search(r"search|ground|tool|billing|tier", msg, re.I):
        raise PermissionError(msg)
    raise GeminiError(f"HTTP {status}: {msg}")


def _log_usage(kind, usage, searched, seconds):
    try:
        with open(USAGE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
                                "model": MODEL, "in": usage["in"], "out": usage["out"],
                                "search": bool(searched), "secs": round(seconds, 1)}) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ask(prompt, system=None, search=False, max_tokens=1024, temperature=0.3,
        thinking="minimal", kind="ask"):
    """Returns dict: text, sources [(title, url)], searched (bool), search_note, secs."""
    global _search_unavailable
    if not API_KEY:
        raise GeminiError("GEMINI_API_KEY is not set in .env")
    use_search = search and SEARCH_ON and not _search_unavailable
    note = None
    if search and not SEARCH_ON:
        note = "web search is switched off (GEMINI_SEARCH=off)"
    elif search and _search_unavailable:
        note = "web search unavailable on this key (needs billing enabled)"

    t0 = time.time()
    for attempt in range(2):
        try:
            try:
                text, sources, usage, searched = _call_interactions(
                    prompt, system, use_search, max_tokens, temperature, thinking)
            except LookupError:
                text, sources, usage, searched = _call_legacy(
                    prompt, system, use_search, max_tokens, temperature)
            break
        except PermissionError as e:
            # Search grounding isn't available (usually the free tier): answer without it
            _search_unavailable = True
            use_search = False
            note = "web search unavailable on this key (needs billing enabled), answered without it"
            print(f"[Gemini] ⚠️ Search grounding refused: {str(e)[:150]}")
        except GeminiError as e:
            if attempt == 0 and ("network" in str(e) or "HTTP 5" in str(e)):
                time.sleep(2)
                continue
            raise
    else:
        raise GeminiError("no answer after retry")

    secs = time.time() - t0
    if not text:
        raise GeminiError("empty answer")
    _log_usage(kind, usage, searched, secs)
    seen, uniq = set(), []
    for title, url in sources:
        if url not in seen:
            seen.add(url)
            uniq.append((title, url))
    print(f"[Gemini] ✅ {MODEL} answered in {secs:.1f}s ({usage['in']} in / {usage['out']} out"
          f"{', web search' if searched else ''})")
    return {"text": text, "sources": uniq[:6], "searched": searched or bool(uniq),
            "search_note": note, "secs": secs}


def generate(model=None, prompt="", options=None):
    """Same shape as ollama.generate, so the maths solver can use Gemini directly."""
    options = options or {}
    r = ask(prompt, max_tokens=max(256, int(options.get("num_predict", 200))),
            temperature=float(options.get("temperature", 0)), thinking="low", kind="maths")
    return {"response": r["text"]}


def month_summary():
    """Totals for the current month and an estimated cost in USD."""
    month = time.strftime("%Y-%m")
    calls = tin = tout = searches = 0
    cost = 0.0
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not str(d.get("time", "")).startswith(month):
                    continue
                calls += 1
                tin += d.get("in", 0)
                tout += d.get("out", 0)
                searches += 1 if d.get("search") else 0
                pin, pout = _price(d.get("model"))
                cost += d.get("in", 0) / 1e6 * pin + d.get("out", 0) / 1e6 * pout
    except OSError:
        pass
    search_cost = max(0, searches - SEARCH_FREE_PER_MONTH) / 1000 * SEARCH_PRICE_PER_1000
    return {"month": month, "calls": calls, "in": tin, "out": tout, "searches": searches,
            "token_cost": cost, "search_cost": search_cost, "total": cost + search_cost}


if __name__ == "__main__":
    # Quick check from the project folder:  python gemini_brain.py
    print(f"Model: {MODEL} | search: {'on' if SEARCH_ON else 'off'} | key set: {available()}")
    r = ask("In one sentence, what is today's date and one current headline?", search=True,
            max_tokens=200, kind="check")
    print("\n" + r["text"])
    if r["search_note"]:
        print("\nNote:", r["search_note"])
    for t, u in r["sources"]:
        print("-", t, u)
    print("\n✅ Web search works with this model." if r["sources"] else
          "\n⚠️ No sources returned: web search may not work with this model. "
          "Try GEMINI_MODEL=gemini-3.8-flash in .env and run this again.")
