"""
continuity.py - keeps your answers and posts coherent over time. Used by
telegram_bot.py (answer + Moltbook post) and telegram_bot_x.py (also LinkedIn).

Every question either bot handles is saved in post_history.db (a small SQLite
database in this folder). Before writing anything new, DeepSeek compares the new
question with the LAST 3 entries:

  - related   -> the answer, the Moltbook post and the LinkedIn post continue the
                 thread: they refer back once, keep the same positions, build on it.
  - unrelated -> flagged as a new topic and written fresh.

Telegram tells you which way it went, e.g.
  🔗 Continues your question of 10 Oct: "Will Thailand's ETS raise costs?"
  🆕 New topic: not connected to your last 3 questions.

Check the history any time:   python continuity.py
"""
import json
import os
import re
import sqlite3
import time

from dotenv import load_dotenv

import cloud_brain

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))
DB_FILE = os.path.join(SCRIPT_DIR, "post_history.db")
LOOKBACK = 3


def _db():
    con = sqlite3.connect(DB_FILE)
    con.execute("""CREATE TABLE IF NOT EXISTS posts (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       created TEXT, question TEXT, tagline TEXT,
                       summary TEXT, moltbook_url TEXT)""")
    cols = {r[1] for r in con.execute("PRAGMA table_info(posts)")}
    if "kind" not in cols:
        con.execute("ALTER TABLE posts ADD COLUMN kind TEXT DEFAULT 'linkedin'")
    return con


def _insert(question, opening, summary, url, kind):
    try:
        with _db() as con:
            con.execute("INSERT INTO posts (created, question, tagline, summary, moltbook_url, kind) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (time.strftime("%Y-%m-%d %H:%M"), (question or "")[:500], (opening or "")[:200],
                         re.sub(r"\s+", " ", summary or "")[:600], url or "", kind))
    except sqlite3.Error as e:
        print(f"[Continuity] ⚠️ Could not save to history: {e}")


def record_post(question, post_text, moltbook_url=None):
    """Saves a finished LinkedIn post (question, opening line, first ~600 chars)."""
    text = (post_text or "").strip()
    _insert(question, text.split("\n", 1)[0], text, moltbook_url, "linkedin")


def record_turn(question, answer, moltbook_title=None, moltbook_url=None):
    """Saves a Telegram question with its answer and the Moltbook post title."""
    _insert(question, moltbook_title or question, answer, moltbook_url, "telegram")


def recent(n=LOOKBACK):
    try:
        with _db() as con:
            rows = con.execute("SELECT created, question, tagline, summary, moltbook_url "
                               "FROM posts ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    except sqlite3.Error:
        return []
    return [dict(zip(("created", "question", "tagline", "summary", "url"), r)) for r in rows]


PROMPT = """You keep an author's answers and posts coherent over time.

His last {n} questions and posts, newest first:
{history}

His NEW question is: "{question}"
{answer}

Is the new question a genuine continuation of one of these (same subject, a follow-up, the
next step of the same argument, or a direct consequence)? Merely sharing a broad field such as
"ESG" or "economics" does NOT count.

Reply with JSON only:
{{"related": true or false,
 "post": <the number of the related entry, 1 = newest, or 0>,
 "reason": "<one short sentence>",
 "bridge": "<if related: one natural sentence, in his voice, that the new post can use to
 link back, e.g. 'Last week I argued that ...; the next question is ...'. Otherwise empty>"}}"""


def check_continuity(question, answer=""):
    """Returns {"related": bool, "previous": {...} or None, "bridge": str, "reason": str}."""
    history = recent()
    result = {"related": False, "previous": None, "bridge": "", "reason": ""}
    if not history:
        result["reason"] = "no earlier questions yet"
        return result
    if not cloud_brain.available():
        result["reason"] = "cloud brain unavailable, treated as a new topic"
        return result
    listing = "\n".join(f"{i}. ({h['created'][:10]}) question: {h['question']}\n"
                        f"   title/opening: {h['tagline']}\n   extract: {h['summary'][:350]}"
                        for i, h in enumerate(history, 1))
    try:
        raw = cloud_brain.ask(PROMPT.format(n=len(history), history=listing, question=question,
                                            answer=(f"Key facts for it:\n{answer[:1200]}" if answer else "")),
                              max_tokens=250, temperature=0.1, kind="continuity")["text"]
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else {}
    except Exception as e:
        print(f"[Continuity] ⚠️ Check failed ({e}); treating as a new topic.")
        result["reason"] = "check failed, treated as a new topic"
        return result
    idx = int(d.get("post") or 0) if str(d.get("post", "0")).strip().isdigit() else 0
    if d.get("related") and 1 <= idx <= len(history):
        result.update(related=True, previous=history[idx - 1],
                      bridge=str(d.get("bridge", "")).strip(), reason=str(d.get("reason", "")))
    else:
        result["reason"] = str(d.get("reason", "")) or "not connected to the last questions"
    print(f"[Continuity] {'🔗 related' if result['related'] else '🆕 new topic'}: {result['reason']}")
    return result


def describe(c):
    """One line for Telegram."""
    if c and c.get("related") and c.get("previous"):
        p = c["previous"]
        when = time.strftime("%d %b", time.strptime(p["created"][:10], "%Y-%m-%d"))
        return f"🔗 Continues your question of {when}: \"{p['question'][:90]}\""
    return f"🆕 New topic: not connected to your last {LOOKBACK} questions" + (
        f" ({c['reason']})" if c and c.get("reason") else "")


def prompt_note(c, what="answer"):
    """Text to add to a prompt when the new question continues an earlier one."""
    if not c or not c.get("related") or not c.get("previous"):
        return ""
    p = c["previous"]
    return (f"\nCONTINUITY: this {what} follows on from the user's earlier question of "
            f"{p['created'][:10]}: \"{p['question']}\". "
            f"What was said then (extract): {p['summary'][:450]}\n"
            f"Refer back to it once, briefly and naturally (for example \"Following on from "
            f"...\"), stay consistent with it, and build on it rather than repeating it.\n")


if __name__ == "__main__":
    rows = recent(10)
    if not rows:
        print("No posts recorded yet. They are saved after each LinkedIn post the bot writes.")
    for r in rows:
        print(f"{r['created']}  {r['question'][:70]}\n    {r['tagline'][:100]}")
