"""
web_search.py - web search for the Telegram path (DeepSeek has no built-in search).

Providers, tried in order (SEARCH_PROVIDERS in .env, default "ddg,brave"):
  ddg    DuckDuckGo via the free `ddgs` package (Python 3.10+: pip install ddgs),
         or the older `duckduckgo-search` package on older Python. No key.
         Unofficial, so it can be rate-limited; then the next provider is used.
  brave  Brave Search API. Needs BRAVE_API_KEY (search.brave.com/api).
         $5 free credit a month (about 1,000 searches), then $5 per 1,000.

Settings (.env)
  SEARCH_PROVIDERS   ddg,brave
  SEARCH_REGION      au-en  (DuckDuckGo region; Brave uses SEARCH_COUNTRY=AU)
  BRAVE_API_KEY      optional backup
"""
import json
import os
import re
import time

import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

VERSION = "4 (2026-10-09, fresh session per DuckDuckGo method)"

PROVIDERS = [p.strip().lower() for p in os.environ.get("SEARCH_PROVIDERS", "ddg,brave").split(",") if p.strip()]
REGION = os.environ.get("SEARCH_REGION", "au-en")
COUNTRY = os.environ.get("SEARCH_COUNTRY", "AU")
BRAVE_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
LOG_FILE = os.path.join(SCRIPT_DIR, "search_usage.jsonl")

_cooldown = {}   # provider -> time until which it is skipped (after rate limits)


def _clean(text, n=400):
    return " ".join((text or "").split())[:n]


def _ddg(query, max_results, news):
    legacy = False
    try:
        from ddgs import DDGS                    # current package (Python 3.10+)
    except ImportError:
        try:
            from duckduckgo_search import DDGS   # older package, works on older Python
            legacy = True
        except ImportError:
            raise RuntimeError("DuckDuckGo package not installed: run  "
                               "python -m pip install ddgs   (Python 3.10+) or  "
                               "python -m pip install duckduckgo-search==6.4.2")
    out = []
    if legacy:
        # The old package's default "api" method no longer works reliably; try its
        # "html" and "lite" methods first. A fresh session per method, because after
        # one error the old package refuses every later call on the same session.
        last_err, rows = None, []
        for backend in ("html", "lite", "api"):
            try:
                with DDGS(timeout=15) as d:
                    rows = d.text(query, region=REGION, safesearch="moderate",
                                  backend=backend, max_results=max_results) or []
            except Exception as e:
                last_err = e
                print(f"[Search]    duckduckgo '{backend}' method failed: {_reason(e)}")
                continue
            if rows:
                break
        if not rows and last_err:
            raise RuntimeError(_reason(last_err))
    else:
        with DDGS(timeout=15) as d:
            rows = d.text(query, region=REGION, safesearch="moderate", max_results=max_results) or []
    for r in rows:
        if r.get("href"):
            out.append({"title": _clean(r.get("title"), 120), "url": r["href"],
                        "snippet": _clean(r.get("body")), "date": ""})
    if news and not legacy:
        try:
            with DDGS(timeout=15) as d:
                for r in d.news(query, region=REGION, safesearch="moderate",
                                timelimit="m", max_results=3) or []:
                    if r.get("url"):
                        out.append({"title": _clean(r.get("title"), 120), "url": r["url"],
                                    "snippet": _clean(r.get("body")),
                                    "date": (r.get("date") or "")[:10]})
        except Exception:
            pass   # news is a bonus; text results are enough
    return out


def _reason(e):
    """Error text without long URLs, so the actual reason is visible."""
    msg = re.sub(r"https?://\S+", "<url>", str(e)).strip()
    return f"{type(e).__name__}: {msg}"[:200]


def _brave(query, max_results, news):
    if not BRAVE_KEY:
        raise RuntimeError("BRAVE_API_KEY not set")
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                     params={"q": query, "count": max_results, "country": COUNTRY,
                             "safesearch": "moderate"},
                     headers={"X-Subscription-Token": BRAVE_KEY, "Accept": "application/json"},
                     timeout=15)
    if r.status_code == 429:
        raise RuntimeError("Brave rate limit or monthly credit used up (429)")
    if r.status_code >= 400:
        raise RuntimeError(f"Brave HTTP {r.status_code}: {r.text[:150]}")
    d = r.json()
    out = []
    for x in ((d.get("web") or {}).get("results") or [])[:max_results]:
        if x.get("url"):
            out.append({"title": _clean(x.get("title"), 120), "url": x["url"],
                        "snippet": _clean(x.get("description")),
                        "date": _clean(x.get("page_age") or x.get("age") or "", 10)})
    return out


_IMPL = {"ddg": _ddg, "brave": _brave}


def search(query, max_results=6, news=False):
    """Returns (results, provider_used, errors). results: list of
    {title, url, snippet, date}. Never raises: empty results on total failure."""
    query = _clean(query, 300)
    errors = []
    for p in PROVIDERS:
        fn = _IMPL.get(p)
        if not fn:
            continue
        if _cooldown.get(p, 0) > time.time():
            errors.append(f"{p}: cooling down after a rate limit")
            continue
        t0 = time.time()
        try:
            res = fn(query, max_results, news)
        except Exception as e:
            msg = f"{p}: {_reason(e)}"
            errors.append(msg)
            print(f"[Search] ⚠️ {msg}")
            if "rate" in str(e).lower() or "429" in str(e) or "202" in str(e):
                _cooldown[p] = time.time() + 600   # skip this provider for 10 minutes
            continue
        # de-duplicate by URL
        seen, uniq = set(), []
        for r in res:
            if r["url"] not in seen:
                seen.add(r["url"])
                uniq.append(r)
        if uniq:
            _log(p, query, len(uniq), time.time() - t0)
            print(f"[Search] ✅ {p}: {len(uniq)} results in {time.time() - t0:.1f}s for: {query[:80]}")
            return uniq, p, errors
        errors.append(f"{p}: no results")
    return [], None, errors


def _log(provider, query, n, secs):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "provider": provider,
                                "query": query[:200], "results": n, "secs": round(secs, 1)}) + "\n")
    except OSError:
        pass


def month_counts():
    month = time.strftime("%Y-%m")
    counts = {}
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if str(d.get("time", "")).startswith(month):
                    counts[d.get("provider")] = counts.get(d.get("provider"), 0) + 1
    except OSError:
        pass
    return counts


def format_for_prompt(results):
    """Numbered results for the model, clearly marked as data, not instructions."""
    lines = []
    for i, r in enumerate(results, 1):
        date = f" ({r['date']})" if r.get("date") else ""
        lines.append(f"[{i}] {r['title']}{date}\n    {r['url']}\n    {r['snippet']}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Quick check:  python web_search.py "latest RBA cash rate"
    import sys
    q = " ".join(sys.argv[1:]) or "latest news Australia today"
    print(f"web_search.py version {VERSION}")
    res, used, errs = search(q, news=True)
    print(f"\nProvider: {used} | providers tried: {PROVIDERS}")
    for e in errs:
        print("  note:", e)
    print(format_for_prompt(res) or "No results.")
    print("\n✅ Web search works." if res else "\n❌ No provider returned results (see notes above).")
