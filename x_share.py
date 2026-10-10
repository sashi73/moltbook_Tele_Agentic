"""
x_share.py - turns a Moltbook post into an X (Twitter) post and a one-tap
"Post to X" link. Used only by telegram_bot_x.py; nothing else imports it.

No X developer account or API key is needed: the link opens X with the text
already typed in, signed in as you, and nothing is posted until you press Post.

Settings (.env, all optional)
  X_INCLUDE_LINK   off (default) or on - add the Moltbook post link to the X post.
                   X tends to show posts with links to fewer people.
  X_HASHTAGS       added at the end if they fit. Default "#Opinion".
                   Set it empty (X_HASHTAGS=) for none.
"""
import os
import re
import unicodedata
from urllib.parse import quote

from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

X_LIMIT = 280
URL_WEIGHT = 23            # X counts every link as 23 characters
INCLUDE_LINK = os.environ.get("X_INCLUDE_LINK", "off").strip().lower() in ("on", "yes", "true", "1")
HASHTAGS = os.environ.get("X_HASHTAGS", "#Opinion").strip()
DEFAULT_ASK = "What's your take?"


def x_length(text):
    """Length as X counts it: links are 23, most Latin text is 1 per character,
    emoji and Asian scripts are 2."""
    text = unicodedata.normalize("NFC", text)
    total = 0
    for part in re.split(r"(https?://\S+)", text):
        if re.match(r"https?://", part):
            total += URL_WEIGHT
            continue
        for ch in part:
            cp = ord(ch)
            light = (cp <= 0x10FF or 0x2000 <= cp <= 0x200D or 0x2010 <= cp <= 0x201F
                     or 0x2032 <= cp <= 0x2037)
            total += 1 if light else 2
    return total


def _shorten(sentence, room):
    """Cut a sentence at a word boundary so it fits in `room` characters."""
    if x_length(sentence) <= room:
        return sentence
    words = sentence.split()
    while words and x_length(" ".join(words) + "...") > room:
        words.pop()
    return (" ".join(words).rstrip(",;:") + "...") if words else ""


def split_moltbook_body(body):
    """brain.py's post body is: hook, blank line, ask, blank line, call to reply."""
    parts = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    hook = parts[0] if parts else ""
    ask = parts[1] if len(parts) > 1 and parts[1].endswith("?") else ""
    return hook, ask


def build_opinion_post(take, title, moltbook_url=None, include_link=None):
    """The short format: your one-line opinion, then the question, then hashtags.
        Borrowing to pay salaries is a slow-motion trap, not a policy.

        Is it ever sustainable? #Opinion"""
    include_link = INCLUDE_LINK if include_link is None else include_link
    take, title = (take or "").strip(), (title or "").strip()
    tail = f"\n\n{moltbook_url}" if (include_link and moltbook_url) else ""
    tags = f" {HASHTAGS}" if HASHTAGS else ""
    text = f"{take}\n\n{title}{tags}{tail}"
    if x_length(text) <= X_LIMIT:
        return text
    text = f"{take}\n\n{title}{tail}"
    if x_length(text) <= X_LIMIT:
        return text
    room = X_LIMIT - x_length(f"{take}\n\n{tail}")
    return f"{take}\n\n{_shorten(title, room)}{tail}"


def build_x_post(title, body, moltbook_url=None, include_link=None):
    """Title + hook + question, kept within 280 characters.
    Drops or trims the least important part first: hashtags, then the long
    question (replaced by a short one), then trims the hook."""
    include_link = INCLUDE_LINK if include_link is None else include_link
    hook, ask = split_moltbook_body(body)
    title = (title or "").strip()
    tail = f"\n\n{moltbook_url}" if (include_link and moltbook_url) else ""

    def assemble(h, a, tags):
        parts = [title] + [p for p in (h, a) if p]
        text = "\n\n".join(parts) + tail
        return text + (f"\n\n{tags}" if tags else "")

    for a in (ask, DEFAULT_ASK):
        for tags in ((HASHTAGS, "") if HASHTAGS else ("",)):
            text = assemble(hook, a, tags)
            if x_length(text) <= X_LIMIT:
                return text

    # Still too long: keep the short question and trim the hook to fit.
    room = X_LIMIT - x_length(assemble("", DEFAULT_ASK, "")) - 2
    text = assemble(_shorten(hook, room), DEFAULT_ASK, "")
    if x_length(text) <= X_LIMIT:
        return text
    return _shorten(title, X_LIMIT - x_length(tail)) + tail   # last resort


def intent_url(text):
    """A link that opens X's compose box with the text filled in."""
    return "https://x.com/intent/post?text=" + quote(text, safe="")


if __name__ == "__main__":
    # Quick check:  python x_share.py
    t = "If emoluments are opex, is borrowing to pay salaries ever sustainable?"
    b = ("Australia's public debt interest is its fastest-growing spending area, so recurring "
         "staff costs now compete directly with interest and capital.\n\n"
         "Should recurring emoluments ever be deficit-funded, or is that always unsustainable?\n\n"
         "Reply with your take, ideally with a figure or a source.")
    post = build_x_post(t, b, "https://www.moltbook.com/post/example")
    print(post)
    print(f"\n[{x_length(post)}/{X_LIMIT} characters]")
    print("\nLink:", intent_url(post)[:120] + "...")
