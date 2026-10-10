"""
linkedin_writer.py - writes a rich LinkedIn post in YOUR style, with researched
facts. Used only by telegram_bot_x.py.

How it works
  DeepSeek searches the web for current facts (figures, laws, institutions,
  dates), then writes the post imitating the posts in linkedin_style.txt.
  Put one or more of your own past LinkedIn posts in that file (separate posts
  with a line containing only ---). The more examples, the more it sounds like you.

Settings (.env, optional)
  LINKEDIN_HASHTAGS   hashtags you always want, e.g. "#ESG" (merged with the 4-5
                      topic hashtags the writer picks; at most 5 in total)
  LINKEDIN_WORDS      target length in words, default 380
  SIGNATURE_NAME      name in the italic sign-off, default "Sashi Kumar Sivam"
  SIGNATURE_LINE      words above the name, default "From the Desk of"
"""
import os
import re

from dotenv import load_dotenv

import cloud_brain

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

STYLE_FILE = os.path.join(SCRIPT_DIR, "linkedin_style.txt")
HASHTAGS = os.environ.get("LINKEDIN_HASHTAGS", "").strip()
WORDS = int(os.environ.get("LINKEDIN_WORDS", "380"))
MAX_CHARS = 2950          # LinkedIn's limit is 3,000
SIGNATURE_NAME = os.environ.get("SIGNATURE_NAME", "Sashi Kumar Sivam").strip()
SIGNATURE_LINE = os.environ.get("SIGNATURE_LINE", "From the Desk of").strip()


# ---------------------------------------------------------------------------
# Sign-off and P.S.
# LinkedIn has no italics, so the sign-off uses Unicode letter styles that show
# as italic and handwritten-script letters on every device.
# ---------------------------------------------------------------------------
def _styled(text, upper_a, lower_a):
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(upper_a + ord(ch) - ord("A")))
        elif "a" <= ch <= "z":
            out.append(chr(lower_a + ord(ch) - ord("a")))
        else:
            out.append(ch)
    return "".join(out)


def italic(text):          # 𝘍𝘳𝘰𝘮 𝘵𝘩𝘦 𝘋𝘦𝘴𝘬 𝘰𝘧
    return _styled(text, 0x1D608, 0x1D622)


def script(text):          # 𝓢𝓪𝓼𝓱𝓲 𝓚𝓾𝓶𝓪𝓻 𝓢𝓲𝓿𝓪𝓶
    return _styled(text, 0x1D4D0, 0x1D4EA)


def signature():
    return f"✒️ {italic(SIGNATURE_LINE)}\n{script(SIGNATURE_NAME)}"


# Moltbook is a social network where only AI agents post and reply. Each line
# invites readers to watch the agents debate the same question. {url} = the post.
PS_LINES = [
    "P.S. I put this exact question to a room with no humans in it. On Moltbook, the new "
    "social network where AI agents do all the talking, the bots are debating it right now, "
    "and some of their takes are sharper than you would expect: {url}",
    "P.S. Curious what the machines make of this? I posed the same question on Moltbook, a new "
    "channel where only AI agents post and argue. Pull up a chair and eavesdrop: {url}",
    "P.S. Humans are not the only ones arguing about this. Over on Moltbook, the new network "
    "built for AI agents, the robots have opened their own thread on it. Worth a look: {url}",
    "P.S. Same question, very different crowd. I dropped it into Moltbook, the social channel "
    "run entirely by AI agents, to see where the bots land. Judge their reasoning for yourself: {url}",
    "P.S. A second opinion from an unusual panel: the AI agents on Moltbook, a new channel where "
    "the bots hold the floor, are working through this same question here: {url}",
]


def finish_post(text, moltbook_url=None):
    """Adds the sign-off and, if the question went live on Moltbook, a P.S. with
    its link, both placed before the hashtag line. Keeps within LinkedIn's limit."""
    import random
    lines = (text or "").rstrip().split("\n")
    tags = ""
    if lines and re.fullmatch(r"(#\w+[\s,]*)+", lines[-1].strip()):
        tags = lines.pop().strip()
    body = "\n".join(lines).rstrip()
    extras = [signature()]
    if moltbook_url:
        extras.append(random.choice(PS_LINES).format(url=moltbook_url))
    tail = "\n\n" + "\n\n".join(extras) + ("\n\n" + tags if tags else "")
    room = MAX_CHARS - len(tail)
    if len(body) > room:
        cut = body[:room]
        body = cut[:cut.rfind(".") + 1] if "." in cut else cut
    return body + tail


def _style_examples():
    try:
        with open(STYLE_FILE, encoding="utf-8") as f:
            posts = [p.strip() for p in re.split(r"^\s*---\s*$", f.read(), flags=re.M) if p.strip()]
        return posts[:3]
    except OSError:
        return []


SYSTEM = """You ghost-write LinkedIn posts for a senior ESG and sustainability consultant
(20+ years in programme leadership, carbon markets, ESG reporting and public policy across
Australia, Southeast Asia, the Middle East and Africa). You write in HIS voice, matching the
style of his example posts: authoritative, analytical, policy and finance framing, dense with
specific named institutions, laws, programmes, standards and figures, and a clear business
implication. Readers must never suspect the post was machine-written."""

# Words and phrases that make a post read as machine-written. Listed in the prompt and,
# as a safety net, replaced after writing.
AI_TELLS = {
    "delve into": "look at", "delves into": "looks at", "delve": "dig",
    "in today's rapidly evolving": "in today's", "rapidly evolving landscape": "fast-moving market",
    "ever-evolving": "changing", "navigating the complexities of": "working through",
    "navigate the complexities of": "work through", "a testament to": "proof of",
    "tapestry": "mix", "game-changer": "turning point", "game changer": "turning point",
    "it's important to note that": "", "it is important to note that": "",
    "in conclusion,": "", "in summary,": "", "moreover,": "beyond that,",
    "furthermore,": "on top of that,", "unlock the potential": "realise the value",
    "paradigm shift": "structural shift", "seamlessly": "smoothly",
    "synergy": "alignment", "realm": "field", "underscores": "shows",
}


def _continuity_block(c):
    if not c or not c.get("related") or not c.get("previous"):
        return ""
    p = c["previous"]
    return f"""
CONTINUITY (important): this post continues his earlier post of {p['created'][:10]}.
Earlier post opening: "{p['tagline']}"
Earlier post extract: {p['summary'][:500]}
Suggested link-back: {c.get('bridge') or '(write your own)'}
Refer back to that earlier post ONCE, naturally, in paragraph 1 (for example "In my last post
I argued that ..."). Keep his positions consistent with it, build on it with new facts, and do
not repeat its content.
"""


def _prompt(question, answer, examples, continuity=None):
    ex = "".join(f"\n=== HIS EXAMPLE POST {i} (style only, do not reuse its content) ===\n{e}\n"
                 for i, e in enumerate(examples, 1))
    tells = ", ".join(sorted({k.strip(" ,") for k in AI_TELLS}))
    return f"""Write a LinkedIn post on this topic: "{question}"

Background research (use these facts; search the web for more current, specific facts:
figures with dates, named laws, regulators, programmes, standards, companies):
---
{answer[:3500]}
---
{ex}{_continuity_block(continuity)}
Structure, exactly in this order:
1. TAGLINE: one imaginative, quotable opening line of 6 to 14 words that stops the scroll and
   captures the big idea, like a sharp magazine headline or a clever turn of phrase (for
   example "Carbon has left the sustainability report and moved onto the balance sheet.").
   No hashtags, no question mark, no colon, no quotation marks.
2. A blank line, then about {WORDS} words in 3 paragraphs in his style:
   - Paragraph 1: what is changing and why it matters, with the drivers (named policies,
     institutions, dates, numbers).
   - Paragraph 2: the mechanism or governance underneath it ("At the heart of this..."),
     with technical specifics a practitioner would recognise.
   - Paragraph 3: the business or policy implication ("However, ... a clear implication: ..."),
     the cost or risk, and what leaders who act early gain.
3. A blank line, then ONE short, specific question to readers that invites comments
   (for example "How is your organisation preparing for ...?").
4. A blank line, then ONE line of 4 to 5 hashtags: a mix of 2 broad ones (like #ESG
   #Sustainability) and 2 or 3 sharp, topic-specific ones (like #CarbonPricing #ASEANNetZero).
   CamelCase, no spaces inside a hashtag.

Writing rules:
- Sound like a seasoned human expert. Vary sentence length; mix one or two short, punchy
  sentences in with the longer ones.
- NEVER use em dashes or en dashes, and never use " - " as a dash. Use commas, colons,
  semicolons or brackets instead. Hyphenated words like net-zero are fine.
- Never use these words or phrases: {tells}.
- Never mention AI, research tools, searches, sources or that the text was generated.
- No headings, no bullet points, no numbered lists, no emojis, no markdown, no citation
  numbers like [1], no links.
- Use ONLY facts from the research or your web searches. If a figure is approximate, write it
  with ~ (for example ~THB 200/ton). Never invent numbers, quotes or sources.
- Australian/British spelling (organisation, decarbonise).
- Output the post only, nothing before or after it."""


def _strip_dashes(t):
    t = re.sub(r"\s*[—–]\s*", ", ", t)          # em/en dashes -> comma
    t = re.sub(r"(?<=\w)\s+-{1,2}\s+(?=\w)", ", ", t)     # spaced hyphen used as a dash
    t = re.sub(r"(?m)^[ \t]*[-•*][ \t]+", "", t)         # stray bullet markers
    t = re.sub(r",\s*,", ",", t)
    t = re.sub(r",\s*([.;:!?])", r"\1", t)
    return t


def _replace_tells(t):
    for bad, good in sorted(AI_TELLS.items(), key=lambda kv: -len(kv[0])):
        def sub(m, good=good):
            if not good:
                return "\x00"          # removed; the next word may need a capital
            return good[0].upper() + good[1:] if m.group(0)[0].isupper() else good
        t = re.sub(rf"\b{re.escape(bad)}", sub, t, flags=re.I)
    t = re.sub(r"(^|[.!?]\s+)\x00\s*([a-z])", lambda m: m.group(1) + m.group(2).upper(), t,
               flags=re.M)
    t = re.sub(r"\s*\x00\s*", " ", t)
    t = re.sub(r"(?m)^ +", "", t)
    return t


def _tidy(text):
    t = text or ""
    t = re.sub(r"\[\d{1,2}\](\[\d{1,2}\])*", "", t)                  # [1][2] citations
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"(?m)^\s*#{1,6}\s+", "", t)                          # markdown headings
    t = re.sub(r"\*\*|__|`", "", t)                                  # bold, code
    t = re.sub(r"(?<!\w)\*(?=\w)|(?<=\w)\*(?!\w)", "", t)            # *italic*
    t = re.sub(r"(?im)^\s*(tagline|title|post|linkedin post|hashtags)\s*:\s*", "", t)
    t = re.sub(r'(?m)^[ \t]*["“](.+?)["”][ \t]*$', r"\1", t.strip())   # a line in quotes
    t = _strip_dashes(t)
    t = _replace_tells(t)
    t = re.sub(r"[ \t]+([,.;:!?])", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


def _hashtag_line(text):
    """Keep one clean hashtag line at the end, merged with LINKEDIN_HASHTAGS, at most 5."""
    lines = text.rstrip().split("\n")
    found = []
    while lines and (not lines[-1].strip() or re.fullmatch(r"(#\w+[\s,]*)+", lines[-1].strip())):
        found = re.findall(r"#\w+", lines.pop()) + found
    body = "\n".join(lines).rstrip()
    tags, seen = [], set()
    for tag in re.findall(r"#\w+", HASHTAGS) + found:
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return body + ("\n\n" + " ".join(tags[:5]) if tags else "")


def _fit(text):
    if len(text) <= MAX_CHARS:
        return text
    body, _, tags = text.rpartition("\n\n")
    if not tags.startswith("#"):
        body, tags = text, ""
    room = MAX_CHARS - len(tags) - 2
    cut = body[:room]
    body = cut[:cut.rfind(".") + 1] if "." in cut else cut
    return body + ("\n\n" + tags if tags else "")


def write_linkedin_post(question, answer, moltbook_url=None, continuity=None):
    """Returns the finished post (signed, with a P.S. if moltbook_url is given), or
    None if the cloud brain is not available (the caller uses the short format)."""
    if not cloud_brain.available():
        return None
    prompt = _prompt(question, answer, _style_examples(), continuity)
    try:
        res = cloud_brain.ask(prompt, system=SYSTEM, search=cloud_brain.SUPPORTS_SEARCH,
                              max_tokens=1400, temperature=0.6, kind="linkedin")
    except cloud_brain.Error as e:
        print(f"[LinkedIn] ⚠️ Could not write the post: {e}")
        return None
    text = _hashtag_line(_tidy(res.get("text", "")))
    if len(text.split()) < 120:          # too thin to be worth posting
        print("[LinkedIn] ⚠️ The written post was too short; using the short format.")
        return None
    return finish_post(text, moltbook_url)


if __name__ == "__main__":
    # Quick check:  python linkedin_writer.py "Is Australia ready for mandatory climate reporting?"
    import sys
    q = " ".join(sys.argv[1:]) or "Is Australia ready for mandatory climate-related financial reporting?"
    post = write_linkedin_post(q, "(no prior research; search the web)",
                               "https://www.moltbook.com/post/example")
    print(post or "Cloud brain not available - check DEEPSEEK_API_KEY in .env")
    if post:
        print(f"\n[{len(post.split())} words, {len(post)} characters]")
