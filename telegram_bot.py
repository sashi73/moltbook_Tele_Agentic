import os
import json
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from functools import partial
from brain import answer_question_detailed, post_to_moltbook, get_replies, TELEGRAM_BRAIN
import cloud_brain
try:   # coherence with the last 3 questions (continuity.py); the bot works without it
    from continuity import check_continuity, record_turn, describe as describe_continuity
except Exception as _e:
    check_continuity = record_turn = describe_continuity = None
    print(f"ℹ️ Continuity check off ({_e})")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

TELEGRAM_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Add it to your .env file.")
if TELEGRAM_TOKEN == (os.environ.get("TELEGRAM_BOT_TOKEN_X") or "").strip():
    raise RuntimeError("TELEGRAM_BOT_TOKEN holds the same token as TELEGRAM_BOT_TOKEN_X (the X bot). "
                       "Put this bot's own token on TELEGRAM_BOT_TOKEN. Run:  python check_bots.py")

MAX_MSG_LEN = 4000
CHAT_FILE = os.path.join(SCRIPT_DIR, "telegram_chat.json")


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    await update.message.reply_text(
        "Agent online. Ask me anything. I'll answer you here first, then post "
        "the question to Moltbook so other agents can add to it.\n"
        "Use /replies to see what they said.\n\n"
        "This chat is now linked: Moltbook heartbeat reports will arrive here."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/start - greet the bot\n"
        "/help - show this message\n"
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_chat(update)
    question = (update.message.text or "").strip()
    if not question:
        await update.message.reply_text("Please send a question.")
        return

    loop = asyncio.get_running_loop()
    fast = TELEGRAM_BRAIN in ("cloud", "deepseek", "gemini") and cloud_brain.available()
    await update.message.reply_text("Thinking..." if fast else "Thinking... (this can take 1-3 minutes)")

    # 0. Coherence: does this follow on from one of the last 3 questions?
    cont = None
    if check_continuity:
        try:
            cont = await loop.run_in_executor(None, check_continuity, question)
            await update.message.reply_text(describe_continuity(cont))
        except Exception as e:
            print(f"[Continuity] ⚠️ {e}")

    # 1. Answer the user first (DeepSeek with web search; Ollama if it is unavailable)
    try:
        res = await loop.run_in_executor(None, partial(answer_question_detailed, question,
                                                       continuity=cont))
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

    # 2 + 3. Post to Moltbook (the same brain writes the post and reads the maths check)
    await update.message.reply_text("📮 Posting your question to Moltbook...")
    used = res["brain"] or "ollama"
    try:
        post = await loop.run_in_executor(None, partial(post_to_moltbook, question, answer,
                                                        llm=used, continuity=cont))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Moltbook error: {e}")
        return
    if record_turn:
        record_turn(question, answer, post.get("title"), post.get("url") if post.get("ok") else None)

    if post.get("post_id"):
        context.chat_data["last_post"] = post["post_id"]

    if post.get("ok"):
        await update.message.reply_text(
            f"✅ Posted to Moltbook:\n{post['title']}\n{post['url']}\n\n"
            "Send /replies later to see answers from other agents."
        )
    else:
        await update.message.reply_text(f"⚠️ Not posted to Moltbook: {post.get('error')}")


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
    lines.append("\nThe heartbeat runs on Ollama and costs nothing.")
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
    app.add_handler(CommandHandler("replies", replies_cmd))
    app.add_handler(CommandHandler("chatid", chatid_cmd))
    app.add_handler(CommandHandler("usage", usage_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


if __name__ == "__main__":
    app = run_bot()
    try:
        import requests
        me = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=10).json()["result"]
        print(f"Running as {me.get('first_name')} (@{me.get('username')})")
    except Exception:
        pass
    print("Bot is running. Press Ctrl+C to stop.")
    try:
        app.run_polling()
    except KeyboardInterrupt:
        pass          # Ctrl+C on Windows can interrupt the shutdown itself; nothing is lost
    print("Bot stopped.")
