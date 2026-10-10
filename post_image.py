"""
post_image.py - finds the best picture for your X / LinkedIn post. Used only by
telegram_bot_x.py.

How the picture is chosen (DeepSeek does the thinking; it reads text, not images):
  1. DeepSeek reads the finished post, names its theme and core message, and writes
     3 stock-photo searches (specific -> broad) and a detailed photo description.
  2. REAL PHOTOS (best quality, look authentic on LinkedIn): the searches run on
     Unsplash and/or Pexels. Up to ~30 candidates come back, each with a written
     description of what is in the photo. DeepSeek reads those descriptions and
     picks the one that best illustrates the post, rejecting cheesy stock clichés.
  3. AI PICTURE (if no stock key, or nothing fits): Pollinations draws 2 versions in
     magazine-photo style; the sharper, better-exposed one is kept.
  4. TEXT CARD: last resort, always works.

Every picture is cleaned: fitted to 1200 x 675, any logo strip cut off, all metadata
(EXIF, XMP, C2PA "AI" labels) removed.

Settings (.env)
  UNSPLASH_ACCESS_KEY  free: unsplash.com/developers > New Application > Access Key
  PEXELS_API_KEY       free: pexels.com/api
  IMAGE_SOURCES        order to try, default: stock,ai,card
  POLLINATIONS_TOKEN   optional, free at enter.pollinations.ai

Test:  python post_image.py "Project managers adopting AI copilots"
"""
import io
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

SOURCES = [s.strip().lower() for s in os.environ.get("IMAGE_SOURCES", "stock,ai,card").split(",") if s.strip()]
POLL_TOKEN = os.environ.get("POLLINATIONS_TOKEN", "").strip()
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "").strip()
UNSPLASH_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
OUT_DIR = os.path.join(SCRIPT_DIR, "x_cards")
W, H = 1200, 675
LOGO_STRIP = 120  # extra pixels generated at the bottom of AI pictures, then cut off

# How each theme should look, so the picture fits the story.
THEME_GUIDE = """
- technology, AI, digital, data, automation -> PRODUCTIVITY: focused professionals working
  with laptops and screens in a bright modern workspace, people and technology working together
- project management, delivery, PMO, programmes, agile -> PROJECT MANAGEMENT: a project team
  planning around a whiteboard with sticky notes or a kanban board, a stand-up meeting, a
  project manager leading a planning session
- ESG, sustainability, climate, net zero, decarbonisation -> clean industry and nature: solar or
  wind farms beside industry, a factory transitioning to clean energy, green infrastructure
- carbon markets, carbon pricing, emissions trading, offsets -> industry meets finance:
  industrial chimneys with a financial district behind, a trading floor, forest carbon projects
- finance, economy, budgets, interest rates, investment -> financial district, boardroom,
  analysts reviewing results (no readable numbers)
- policy, regulation, law, government, compliance -> parliament or government building,
  officials signing documents, a formal boardroom
- energy, power, grids, oil and gas, renewables -> power lines, substations, solar arrays,
  LNG terminal, control room
- supply chain, logistics, trade, shipping -> container port with cranes and ships,
  warehouse operations, freight trucks
- leadership, people, workforce, culture -> executives in discussion, mentoring, a diverse team
- anything else -> the most concrete real-world scene the post is about
If the post is about one country or city, prefer a scene set there (Bangkok, Kuala Lumpur,
Singapore, Jakarta, Sydney...).
"""

BRIEF_PROMPT = """Read this LinkedIn post and plan its picture.

POST:
---
{post}
---

Theme guide (pick the theme, use its visual direction):
{guide}

Reply with JSON only:
{{"theme": "<theme, a few words>",
 "message": "<the post's core message in under 12 words>",
 "queries": ["<specific stock photo search, 2-4 words>", "<broader search, 2-3 words>",
             "<safe general search for the theme, 2 words>"],
 "prompt": "<40-70 words describing ONE realistic editorial photograph that illustrates the core
 message: subject, action, setting, composition, light and mood. Concrete and specific.
 No text, words, numbers, logos or recognisable real people.>"}}"""

PICK_PROMPT = """A LinkedIn post needs a photo.
Theme: {theme}
Core message: {message}

Candidate stock photos (their written descriptions):
{listing}

Choose the ONE photo that best illustrates the core message for a senior professional
audience. Prefer authentic, editorial-looking scenes that match the theme and setting.
Reject cheesy stock clichés (handshakes, thumbs up, people pointing at blank screens,
puzzle pieces, light bulbs), unrelated scenes, and anything with visible brand logos.
If none of them fits reasonably well, answer 0.

Reply with JSON only: {{"pick": <number or 0>, "why": "<short reason>"}}"""

AI_STYLE = (", editorial photograph for a business magazine, shot on a full-frame camera with a "
            "35mm lens, natural light, realistic textures, balanced composition, muted professional "
            "colour palette, sharp focus, high detail. No text, no letters, no logos, no watermark, "
            "no distorted faces or hands")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _json(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def _plan(topic, post, ask):
    d = {}
    try:
        d = _json(ask(BRIEF_PROMPT.format(post=(post or topic)[:2600], guide=THEME_GUIDE)))
    except Exception as e:
        print(f"[Image] ⚠️ Could not plan the picture: {e}")
    words = re.findall(r"[A-Za-z]{4,}", topic)
    plan = {
        "theme": str(d.get("theme") or "business"),
        "message": str(d.get("message") or topic[:100]),
        "queries": [" ".join(str(q).split()) for q in (d.get("queries") or []) if str(q).strip()][:3]
                   or [" ".join(words[:3]) or "business meeting"],
        "prompt": " ".join(str(d.get("prompt") or f"Editorial photograph representing {topic[:120]}").split()),
    }
    print(f"[Image] 🧭 Theme: {plan['theme']} | message: {plan['message']}")
    return plan


def _is_image(content):
    try:
        from PIL import Image
        Image.open(io.BytesIO(content)).verify()
        return True
    except Exception:
        return False


def _fit(img, crop_bottom=0):
    from PIL import Image
    img = img.convert("RGB")
    if crop_bottom and img.height > crop_bottom + 200:
        img = img.crop((0, 0, img.width, img.height - crop_bottom))
    target = W / H
    if img.width / img.height > target:                 # too wide: trim the sides
        new_w = int(img.height * target)
        left = (img.width - new_w) // 2
        img = img.crop((left, 0, left + new_w, img.height))
    else:                                               # too tall: keep the upper middle
        new_h = int(img.width / target)
        top = max(0, (img.height - new_h) // 3)
        img = img.crop((0, top, img.width, top + new_h))
    return img.resize((W, H), Image.LANCZOS)


def _save(content, tag, crop_bottom=0):
    """Fits the picture to 1200 x 675 and writes a fresh JPEG with no metadata at all."""
    from PIL import Image
    img = _fit(Image.open(io.BytesIO(content)), crop_bottom)
    clean = Image.frombytes("RGB", img.size, img.tobytes())     # pixels only
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, time.strftime(f"{tag}_%Y%m%d_%H%M%S_") + f"{random.randint(100, 999)}.jpg")
    clean.save(path, "JPEG", quality=92, optimize=True)
    return path


def _quality(content):
    """Rough technical quality: sharpness + contrast, penalising very dark/bright images."""
    from PIL import Image, ImageFilter, ImageStat
    g = Image.open(io.BytesIO(content)).convert("L").resize((400, 225))
    sharp = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]
    st = ImageStat.Stat(g)
    bright, contrast = st.mean[0], st.stddev[0]
    penalty = 0 if 60 <= bright <= 200 else 15
    return sharp * 2 + contrast * 0.5 - penalty


# ---------------------------------------------------------------------------
# Stock photos (Unsplash, Pexels) chosen by DeepSeek from their descriptions
# ---------------------------------------------------------------------------
def _search_unsplash(query, n=10):
    if not UNSPLASH_KEY:
        return []
    try:
        r = requests.get("https://api.unsplash.com/search/photos", timeout=20,
                         headers={"Authorization": f"Client-ID {UNSPLASH_KEY}", "Accept-Version": "v1"},
                         params={"query": query, "orientation": "landscape", "per_page": n,
                                 "content_filter": "high"})
        out = []
        for p in r.json().get("results") or []:
            desc = p.get("description") or p.get("alt_description") or ""
            out.append({"source": "Unsplash", "desc": desc, "url": p["urls"].get("regular"),
                        "by": (p.get("user") or {}).get("name", ""),
                        "track": (p.get("links") or {}).get("download_location")})
        return out
    except Exception as e:
        print(f"[Image] ⚠️ Unsplash: {type(e).__name__}")
        return []


def _search_pexels(query, n=10):
    if not PEXELS_KEY:
        return []
    try:
        r = requests.get("https://api.pexels.com/v1/search", timeout=20,
                         headers={"Authorization": PEXELS_KEY},
                         params={"query": query, "orientation": "landscape", "per_page": n})
        return [{"source": "Pexels", "desc": p.get("alt") or "", "by": p.get("photographer", ""),
                 "url": p["src"].get("large2x") or p["src"].get("landscape"), "track": None}
                for p in r.json().get("photos") or []]
    except Exception as e:
        print(f"[Image] ⚠️ Pexels: {type(e).__name__}")
        return []


def _from_stock(plan, ask):
    if not (UNSPLASH_KEY or PEXELS_KEY):
        return None
    cands, seen = [], set()
    for q in plan["queries"]:
        for c in _search_unsplash(q) + _search_pexels(q):
            key = c["url"]
            if key and key not in seen and len(c["desc"]) > 8:
                seen.add(key)
                cands.append(c)
        if len(cands) >= 24:
            break
    if not cands:
        print("[Image] ⚠️ No stock photos found for: " + " / ".join(plan["queries"]))
        return None
    cands = cands[:30]
    listing = "\n".join(f"{i}. {c['desc'][:160]}" for i, c in enumerate(cands, 1))
    try:
        d = _json(ask(PICK_PROMPT.format(theme=plan["theme"], message=plan["message"], listing=listing)))
        pick = int(d.get("pick") or 0)
    except Exception as e:
        print(f"[Image] ⚠️ Could not rank photos ({e}); using the top search result.")
        pick, d = 1, {}
    if not 1 <= pick <= len(cands):
        print("[Image] ⏭️ DeepSeek found no stock photo that fits; trying an AI picture.")
        return None
    c = cands[pick - 1]
    print(f"[Image] 🏆 Picked #{pick}: {c['desc'][:90]} ({d.get('why', '')[:80]})")
    try:
        if c.get("track"):   # Unsplash asks apps to report downloads
            requests.get(c["track"], timeout=10,
                         headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"})
        img = requests.get(c["url"], timeout=30)
        if img.ok and _is_image(img.content):
            return _save(img.content, "photo"), f"Photo by {c['by']} on {c['source']}"
    except Exception as e:
        print(f"[Image] ⚠️ Download failed: {type(e).__name__}")
    return None


# ---------------------------------------------------------------------------
# AI picture (Pollinations), 2 versions, keep the technically better one
# ---------------------------------------------------------------------------
def _pollinations_once(prompt):
    seed = random.randint(1, 10 ** 7)
    params = (f"width=1600&height={900 + LOGO_STRIP}&seed={seed}&model=flux"
              f"&nologo=true&enhance=false&private=true&safe=true")
    headers = {"Authorization": f"Bearer {POLL_TOKEN}"} if POLL_TOKEN else {}
    urls = [f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}?{params}"]
    if POLL_TOKEN:
        urls.insert(0, f"https://gen.pollinations.ai/image/{quote(prompt, safe='')}?{params}")
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=150)
        except requests.RequestException as e:
            print(f"[Image] ⚠️ Pollinations: {type(e).__name__}")
            continue
        ctype = r.headers.get("content-type", "")
        if r.ok and ctype.startswith("image/") and len(r.content) > 20000 and _is_image(r.content):
            return r.content
        print(f"[Image] ⚠️ Pollinations returned HTTP {r.status_code} {ctype[:30]}")
    return None


def _from_ai(plan):
    prompt = plan["prompt"].rstrip(". ") + AI_STYLE
    with ThreadPoolExecutor(max_workers=2) as ex:
        results = [x for x in ex.map(lambda _: _pollinations_once(prompt), range(2)) if x]
    if not results:
        return None
    best = max(results, key=_quality)
    return _save(best, "pic", crop_bottom=LOGO_STRIP), "Generated picture"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def get_post_image(topic, post_text, ask, card_take=None, card_question=None):
    """Returns (path, credit) or (None, None). The picture is chosen from the FULL post
    text, so it matches the story. `ask(prompt) -> text` is DeepSeek."""
    plan = None
    for source in SOURCES:
        if source in ("stock", "ai") and plan is None:
            plan = _plan(topic, post_text, ask)
        got = None
        if source == "stock":
            got = _from_stock(plan, ask)
        elif source == "ai":
            got = _from_ai(plan)
        elif source == "card":
            try:
                from x_image import make_card
                got = make_card(card_take or topic, card_question or topic), "Text card"
            except Exception as e:
                print(f"[Image] ⚠️ Card: {e}")
        if got:
            print(f"[Image] ✅ {got[1]}: {got[0]}")
            return got
    return None, None


if __name__ == "__main__":
    import sys
    topic = " ".join(sys.argv[1:]) or "Project managers adopting AI copilots to run delivery"
    try:
        import cloud_brain
        ask = lambda p: cloud_brain.ask(p, max_tokens=400, temperature=0.4, kind="image")["text"]
    except Exception:
        ask = lambda p: ""
    print("Stock keys:", "Unsplash" if UNSPLASH_KEY else "-", "Pexels" if PEXELS_KEY else "-")
    path, credit = get_post_image(topic, topic, ask)
    print("Saved:", path, "|", credit)
