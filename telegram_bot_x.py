"""
telegram_bot_x.py - the Telegram bot with "Post to X" and "Post to LinkedIn" buttons.

Does everything telegram_bot.py does (answer, post to Moltbook, maths check),
then sends you a ready-made X post and a LinkedIn post, each with a button.
Tap one, check the text, and press Post. Nothing goes to X or LinkedIn without
you pressing Post.

It is a separate program: brain.py and telegram_bot.py are not changed, so other
people can keep using those as before.

Run:  python telegram_bot_x.py

Settings (.env)
  TELEGRAM_BOT_TOKEN_X   token of the X/LinkedIn bot (Moltbot_AgentX, @SasXMoltbot).
                         Required. It must be a different bot from TELEGRAM_BOT_TOKEN,
                         which belongs to telegram_bot.py (Moltbot_Agent).
  X_INCLUDE_LINK, X_HASHTAGS   see x_share.py
  X_HANDLE, CARD_LABEL         see x_image.py (picture card)
  LINKEDIN_HASHTAGS, LINKEDIN_WORDS   see linkedin_writer.py (style: linkedin_style.txt)
  IMAGE_SOURCES, POLLINATIONS_TOKEN, PEXELS_API_KEY   see post_image.py
  LINKEDIN_INTRO, LINKEDIN_HASHTAGS, LINKEDIN_INCLUDE_LINK   see linkedin_share.py
"""
import os
import re
import json
import asyncio
import threading
from functools import partial

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import brain
from brain import answer_question_detailed, get_replies, TELEGRAM_BRAIN
import cloud_brain
from x_share import build_x_post, build_opinion_post, intent_url, x_length, X_LIMIT
from linkedin_share import build_linkedin_post, LINKEDIN_OPEN_URL
from linkedin_writer import write_linkedin_post, finish_post
from post_image import get_post_image
from continuity import check_continuity, record_post, describe as describe_continuity
import html
import requests
from telegram.error import TelegramError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_X", "").strip()
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN_X is not set in .env. It is the token of "
                       "@SasXMoltbot (from @BotFather > /mybots > API Token).")
if TELEGRAM_TOKEN == os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
    raise RuntimeError("TELEGRAM_BOT_TOKEN_X and TELEGRAM_BOT_TOKEN hold the same token. "
                       "Each bot needs its own. Run:  python check_bots.py")

try:
    from x_image import make_card          # needs: python -m pip install pillow
except Exception as _e:                    # the bot still works without pictures
    make_card = None
    print(f"ℹ️ Picture cards off ({_e}). Install with:  python -m pip install pillow")

MAX_MSG_LEN = 4000
CHAT_FILE = os.path.join(SCRIPT_DIR, "telegram_chat.json")


# ---------------------------------------------------------------------------
# Capture the Moltbook title and body that brain.py writes, so the X post uses
# exactly the same words. This only changes brain.py's behaviour inside THIS
# program; telegram_bot.py and heartbeat.py are unaffected.
# ---------------------------------------------------------------------------
_captured = threading.local()
_original_build = brain.build_moltbook_post


def _build_and_capture(subject, agent_answer, llm="ollama"):
    title, body = _original_build(subject, agent_answer, llm=llm)
    _captured.post = (title, body)
    return title, body


brain.build_moltbook_post = _build_and_capture


def post_and_capture(subject, agent_answer, llm="ollama"):
    """Posts to Moltbook as usual and also returns the title and body used."""
    _captured.post = None
    result = brain.post_to_moltbook(subject, agent_answer, llm=llm)
    captured = _captured.post
    if captured is None:
        # Moltbook posting was paused before a post was written; write one for X anyway.
        try:
            captured = _original_build(subject, agent_answer, llm=llm)
        except Exception as e:
            print(f"[X] ⚠️ Could not write the X post: {e}")
    return result, captured


def write_take(question, answer, title, llm="ollama"):
    """One punchy, first-person opinion line for X and LinkedIn (you can edit it
    before posting)."""
    text = brain._ask(f"""Question: "{question}"
Researched answer:
---
{answer[:2500]}
---
Write ONE line of opinion that a seasoned expert would post on X about this: a clear,
confident stance, in plain words, under 110 characters. It must be fair to the facts
above and must not invent numbers. No hashtags, no emojis, no quotes, no "I think".
Reply with the line only.""", num_predict=80, temperature=0.7, llm=llm)
    line = next((l for l in (text or "").splitlines() if l.strip()), "").strip()
    line = line.strip('"\'“”*').strip()
    line = re.sub(r"\s*[\u2014\u2013]\s*|\s+-\s+", ", ", line)     # no dashes
    line = " ".join(w for w in line.split() if not w.startswith("#"))
    if len(line) > 140:
        line = line[:137].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return line or title


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def remember_chat(update: Update):
    """Saves your chat id so heartbeat.py can send you its reports."""
    chat = update.effective_chat
    if not chat:
        return
    try:
        with open(CHAT_FILE, encoding="utf-8") as f:
            if json.load(f).get("chat_id") == chat.id:
                return
    except (OSError, ValueError):
        pass
    with open(CHAT_FILE, "w", encoding="utf-8") as f:
        json.dump({"chat_id": chat.id,
                   "name": chat.username or chat.first_name or chat.title}, f)
    print(f"Saved Telegram chat id {chat.id} for heartbeat reports.")


def chunk_text(text, size=MAX_MSG_LEN):
    for i in range(0, len(text), size):
        yield text[i:i + size]


async def send_long(update, text):
    for chunk in chunk_text(text):
        await update.message.reply_text(chunk)


async def _send_with_button(message, text, label, url):
    """Text (easy to copy) with a one-tap button; plain text if Telegram refuses the button."""
    try:
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(label, url=url)]]), disable_web_page_preview=True)
    except TelegramError as e:
        print(f"[Share] ⚠️ Button refused by Telegram ({e}); sending text only.")
        await message.reply_text(text + "\n\n(Copy the text above and paste it in.)",
                                 disable_web_page_preview=True)


async def send_card(message, path, credit=None):
    """The picture, to save and attach in X or LinkedIn (their links cannot carry it)."""
    try:
        with open(path, "rb") as f:
            await message.reply_photo(
                f, caption="🖼 Picture for your X and LinkedIn posts: save it (long-press or "
                           "right-click), then attach it before pressing Post."
                           + (f"\n{credit}" if credit else ""))
    except (OSError, TelegramError) as e:
        print(f"[Share] ⚠️ Could not send the picture: {e}")


async def send_x_draft(message, x_text):
    """A short header, then the post ALONE in its own message (so Copy copies only the
    post), with the Post to X button under it."""
    await message.reply_text(f"𝕏 X post below ({x_length(x_text)}/{X_LIMIT} characters). "
                             f"Tap Post to X, or long-press it and Copy.")
    try:
        await message.reply_text(x_text, disable_web_page_preview=True,
                                 reply_markup=InlineKeyboardMarkup(
                                     [[InlineKeyboardButton("𝕏 Post to X", url=intent_url(x_text))]]))
    except TelegramError as e:
        print(f"[Share] ⚠️ X button refused by Telegram ({e}); sending text only.")
        await message.reply_text(x_text, disable_web_page_preview=True)


async def send_linkedin_draft(message, li_text):
    """A short header, then the post ALONE in its own message, so long-press > Copy
    copies only the post. LinkedIn cannot take text through a link, so the button
    just opens LinkedIn; paste there."""
    await message.reply_text("💼 LinkedIn post below. Long-press it and Copy, tap Open LinkedIn, "
                             "Start a post, paste, then add the picture.")
    button = InlineKeyboardMarkup([[InlineKeyboardButton("💼 Open LinkedIn", url=LINKEDIN_OPEN_URL)]])
    try:
        await message.reply_text(li_text, reply_markup=button, disable_web_page_preview=True)
    except TelegramError as e:
        print(f"[Share] ⚠️ LinkedIn button refused ({e}); sending text only.")
        await message.reply_text(li_text, disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    await update.message.reply_text(
        "Agent online (X edition). Ask me anything. I'll answer you here, post the "
        "question to Moltbook, then give you ready-made X and LinkedIn posts with buttons.\n"
        "Use /replies to see what other agents said, /x or /linkedin to get the last post again.\n\n"
        "This chat is now linked: Moltbook heartbeat reports will arrive here."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/start - greet the bot\n"
        "/help - show this message\n"
        "/x - show the last X post and its button again\n"
        "/linkedin - show the last LinkedIn post and its button again\n"
        "/replies - show answers other agents left on your last Moltbook post\n"
        "/chatid - show this chat's id (used for heartbeat reports)\n"
        "/usage - cloud brain calls and estimated cost this month\n\n"
        "Or just send a question, e.g.: Is Gandhi and Mandela similar?"
    )


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    await update.message.reply_text(
        f"This chat's id is {update.effective_chat.id}. It is saved, so heartbeat "
        f"reports will come here."
    )


async def x_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    x_text = context.chat_data.get("last_x")
    if not x_text:
        await update.message.reply_text("No X post yet. Ask a question first.")
        return
    card = context.chat_data.get("last_card")
    if card and os.path.exists(card):
        await send_card(update.message, card, context.chat_data.get("last_credit"))
    await send_x_draft(update.message, x_text)


async def linkedin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    li_text = context.chat_data.get("last_linkedin")
    if not li_text:
        await update.message.reply_text("No LinkedIn post yet. Ask a question first.")
        return
    card = context.chat_data.get("last_card")
    if card and os.path.exists(card):
        await send_card(update.message, card, context.chat_data.get("last_credit"))
    await send_linkedin_draft(update.message, li_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    question = (update.message.text or "").strip()
    if not question:
        await update.message.reply_text("Please send a question.")
        return

    loop = asyncio.get_running_loop()
    fast = TELEGRAM_BRAIN in ("cloud", "deepseek", "gemini") and cloud_brain.available()
    await update.message.reply_text("Thinking..." if fast else "Thinking... (this can take 1-3 minutes)")

    # 1. Answer the user first
    try:
        res = await loop.run_in_executor(None, answer_question_detailed, question)
    except Exception as e:
        await update.message.reply_text(f"❌ Could not answer: {e}")
        return
    answer = res["text"]
    footer = []
    if res.get("sources"):
        footer.append("Sources:\n" + "\n".join(
            (f"{title[:90]}" if title.startswith("[") else f"{i}. {title[:80]}") + f"\n   {url}"
            for i, (title, url) in enumerate(res["sources"][:6], 1)))
    brain_name = (f"{cloud_brain.NAME} ({cloud_brain.MODEL}"
                  + (", web search)" if res.get("sources") else ")")
                  if res["brain"] not in (None, "ollama")
                  else "Ollama (local" + (", web search)" if res.get("sources") else ")"))
    footer.append(f"🧠 {brain_name} · {res['secs']:.0f}s")
    if res.get("note"):
        footer.append(f"ℹ️ {res['note']}")
    await send_long(update, answer + "\n\n" + "\n\n".join(footer))

    # 2. Post to Moltbook (same brain writes the post and reads the maths check)
    await update.message.reply_text("📮 Posting your question to Moltbook...")
    used = res["brain"] or "ollama"
    try:
        post, captured = await loop.run_in_executor(
            None, partial(post_and_capture, question, answer, llm=used))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Moltbook error: {e}")
        return

    if post.get("post_id"):
        context.chat_data["last_post"] = post["post_id"]

    if post.get("ok"):
        await update.message.reply_text(
            f"✅ Posted to Moltbook:\n{post['title']}\n{post['url']}\n\n"
            "Send /replies later to see answers from other agents."
        )
    else:
        await update.message.reply_text(f"⚠️ Not posted to Moltbook: {post.get('error')}")

    # 3. X and LinkedIn posts, written from the same title and hook
    #    (offered even if Moltbook failed)
    if captured:
        title, body = captured
        link = post.get("url") if post.get("ok") else None
        try:
            take = await loop.run_in_executor(None, partial(write_take, question, answer, title, used))
        except Exception as e:
            print(f"[X] ⚠️ Could not write the opinion line: {e}")
            take = ""
        if take:
            x_text = build_opinion_post(take, title, link)
        else:
            x_text = build_x_post(title, body, link)
        context.chat_data["last_x"] = x_text
        await send_x_draft(update.message, x_text)

        # 4. Continuity: is this a follow-on from one of the last 3 posts?
        await update.message.reply_text("✍️ Writing your LinkedIn post (about 30-60 seconds)...")
        try:
            cont = await loop.run_in_executor(None, check_continuity, question, answer)
        except Exception as e:
            print(f"[Continuity] ⚠️ {e}")
            cont = {"related": False, "reason": "check failed"}
        await update.message.reply_text(describe_continuity(cont))

        # 5. A rich LinkedIn post in your style (fresh research, coherent with the thread)
        try:
            li_text = await loop.run_in_executor(
                None, partial(write_linkedin_post, question, answer, link, cont))
        except Exception as e:
            print(f"[LinkedIn] ⚠️ {e}")
            li_text = None
        if not li_text:
            li_text = finish_post(build_linkedin_post(title, body, None, take=take), link)  # short fallback

        # 6. A picture chosen from the finished post, so it matches the story
        await update.message.reply_text("🎨 Choosing a picture that fits the post...")
        try:
            img, credit = await loop.run_in_executor(
                None, partial(get_post_image, question, li_text, partial(_describe_picture, llm=used),
                              take or title, title))
        except Exception as e:
            print(f"[Image] ⚠️ {e}")
            img, credit = None, None
        if img:
            context.chat_data["last_card"] = img
            context.chat_data["last_credit"] = credit
            await send_card(update.message, img, credit)
        context.chat_data["last_linkedin"] = li_text
        await send_linkedin_draft(update.message, li_text)
        record_post(question, li_text, link)


def _describe_picture(prompt, llm="ollama"):
    """DeepSeek reads the post and describes the picture (Ollama only as a fallback)."""
    if cloud_brain.available():
        try:
            return cloud_brain.ask(prompt, max_tokens=450, temperature=0.4, kind="image")["text"]
        except cloud_brain.Error as e:
            print(f"[Image] ⚠️ {e}; using the local brain to describe the picture.")
    return brain._ask(prompt, num_predict=450, temperature=0.4, llm=llm)


async def usage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = cloud_brain.month_summary()
    lines = [f"{cloud_brain.NAME} usage for {u['month']} ({cloud_brain.MODEL}):",
             f"• {u['calls']} calls, {u['in']:,} input + {u['out']:,} output tokens"]
    if u.get("search_breakdown") is not None:
        names = {"ddg": "DuckDuckGo (free)", "brave": "Brave (~1,000 free a month)"}
        parts = ", ".join(f"{names.get(k, k)}: {v}" for k, v in u["search_breakdown"].items()) or "none yet"
        lines.append(f"• {u['searches']} web searches ({parts})")
    elif cloud_brain.SUPPORTS_SEARCH:
        lines.append(f"• {u['searches']} web searches ({cloud_brain.SEARCH_FREE_PER_MONTH:,} free a month)")
    lines.append(f"• Estimated cost: US${u['total']:.4f}"
                 + (f" (incl. US${u['search_cost']:.2f} for searches)" if u.get("search_cost") else ""))
    lines.append("\nX and LinkedIn posts through the buttons are free. The heartbeat runs on Ollama and costs nothing.")
    await update.message.reply_text("\n".join(lines))


async def replies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post_id = context.args[0] if context.args else context.chat_data.get("last_post")
    if not post_id:
        await update.message.reply_text("No Moltbook post yet. Ask a question first.")
        return
    loop = asyncio.get_running_loop()
    try:
        items = await loop.run_in_executor(None, get_replies, post_id)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch replies: {e}")
        return
    if not items:
        await update.message.reply_text("No replies yet. Check back later.")
        return
    text = "\n\n".join(f"🦞 {author}:\n{content[:800]}" for author, content in items)
    await send_long(update, f"Replies on Moltbook ({len(items)}):\n\n{text}")


def run_bot():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("x", x_cmd))
    app.add_handler(CommandHandler("linkedin", linkedin_cmd))
    app.add_handler(CommandHandler("replies", replies_cmd))
    app.add_handler(CommandHandler("chatid", chatid_cmd))
    app.add_handler(CommandHandler("usage", usage_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


def whoami(token):
    try:
        me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10).json()["result"]
        return f"{me.get('first_name')} (@{me.get('username')})"
    except Exception:
        return "unknown (check the token with: python check_bots.py)"


if __name__ == "__main__":
    app = run_bot()
    print(f"Running as {whoami(TELEGRAM_TOKEN)}")
    print("X edition bot (X + LinkedIn) is running. Press Ctrl+C to stop.")
    try:
        app.run_polling()
    except KeyboardInterrupt:
        pass          # Ctrl+C on Windows can interrupt the shutdown itself; nothing is lost
    print("X edition bot stopped.")
