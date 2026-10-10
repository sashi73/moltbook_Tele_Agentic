"""
linkedin_share.py - turns a Moltbook post into a LinkedIn post and a one-tap
"Post to LinkedIn" link. Used only by telegram_bot_x.py.

No LinkedIn developer app or key is needed. LinkedIn does not reliably accept
pre-filled text through a link (it often opens an empty box or the article
editor), so the bot sends the text as a tap-to-copy block and a button that
opens LinkedIn: tap to copy, Start a post, paste, press Post.

Settings (.env, all optional)
  LINKEDIN_INTRO          a line put before the question, e.g.
                          "Question of the day from my AI research agent:"
                          Empty by default.
  LINKEDIN_HASHTAGS       e.g. "#ESG #AI #Sustainability" (3 or fewer works best)
  LINKEDIN_INCLUDE_LINK   off (default) or on - add the Moltbook post link.
                          LinkedIn tends to show posts with links to fewer people.
"""
import os
from urllib.parse import quote

from dotenv import load_dotenv

from x_share import split_moltbook_body

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

INTRO = os.environ.get("LINKEDIN_INTRO", "").strip()
HASHTAGS = os.environ.get("LINKEDIN_HASHTAGS", "").strip()
INCLUDE_LINK = os.environ.get("LINKEDIN_INCLUDE_LINK", "off").strip().lower() in ("on", "yes", "true", "1")
MAX_LEN = 1200      # LinkedIn allows 3,000; shorter posts read better and keep the link short


def build_linkedin_post(title, body, moltbook_url=None, include_link=None, take=None):
    """Optional intro, your one-line view, the question, the key fact, an
    invitation to comment, then optional link and hashtags."""
    include_link = INCLUDE_LINK if include_link is None else include_link
    hook, ask = split_moltbook_body(body)
    parts = []
    if INTRO:
        parts.append(INTRO)
    if take:
        parts.append(take.strip())
    parts.append((title or "").strip())
    if hook:
        parts.append(hook)
    parts.append(ask or "What's your view?")
    parts.append("I'd value your perspective in the comments.")
    if include_link and moltbook_url:
        parts.append(f"The AI agents' discussion: {moltbook_url}")
    if HASHTAGS:
        parts.append(HASHTAGS)
    text = "\n\n".join(p for p in parts if p)
    return text[:MAX_LEN]


LINKEDIN_OPEN_URL = "https://www.linkedin.com/feed/"   # always works; "Start a post" is at the top


if __name__ == "__main__":
    # Quick check:  python linkedin_share.py
    t = "If emoluments are opex, is borrowing to pay salaries ever sustainable?"
    b = ("Australia's public debt interest is its fastest-growing spending area, so recurring "
         "staff costs now compete directly with interest and capital.\n\n"
         "Should recurring emoluments ever be deficit-funded, or is that always unsustainable?\n\n"
         "Reply with your take, ideally with a figure or a source.")
    post = build_linkedin_post(t, b, "https://www.moltbook.com/post/example")
    print(post)
    print(f"\n[{len(post)} characters]")
    print("\nOpen:", LINKEDIN_OPEN_URL)
