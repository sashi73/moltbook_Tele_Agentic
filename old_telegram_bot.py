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
from brain import answer_question, post_to_moltbook, get_replies

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Add it to your .env file.")

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
        "/chatid - show this chat's id (used for heartbeat reports)\n\n"
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
    await update.message.reply_text("Thinking... (this can take 1-3 minutes)")

    # 1. Answer the user first
    try:
        answer = await loop.run_in_executor(None, answer_question, question)
    except Exception as e:
        await update.message.reply_text(f"❌ Could not answer: {e}")
        return
    await send_long(update, answer)

    # 2 + 3. Post to Moltbook (includes the maths check), then report back
    await update.message.reply_text("📮 Posting your question to Moltbook...")
    try:
        res = await loop.run_in_executor(None, post_to_moltbook, question, answer)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Moltbook error: {e}")
        return

    if res.get("post_id"):
        context.chat_data["last_post"] = res["post_id"]

    if res.get("ok"):
        await update.message.reply_text(
            f"✅ Posted to Moltbook:\n{res['title']}\n{res['url']}\n\n"
            "Send /replies later to see answers from other agents."
        )
    else:
        await update.message.reply_text(f"⚠️ Not posted to Moltbook: {res.get('error')}")


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


if __name__ == "__main__":
    app = run_bot()
    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()
