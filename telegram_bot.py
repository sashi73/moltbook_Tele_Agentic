import os
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from brain import think_and_learn

# Load env vars — must happen before reading them
load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is not set. Add it to your .env file."
    )

# Telegram caps messages at 4096 chars; use 4000 to be safe
MAX_MSG_LEN = 4000


def chunk_text(text: str, size: int = MAX_MSG_LEN):
    """Yield chunks of text no longer than `size` characters."""
    for i in range(0, len(text), size):
        yield text[i:i + size]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Agent Online. Send /study <subject> to feed my database."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/start — greet the bot\n"
        "/study <subject> — have the agent learn a topic\n"
        "/help — show this message"
    )


async def study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subject = " ".join(context.args).strip()
    if not subject:
        await update.message.reply_text(
            "Please provide a subject. Usage: /study quantum computing"
        )
        return

    await update.message.reply_text(
        f"Processing '{subject}' using Ollama brain..."
    )

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, think_and_learn, subject)
    except Exception as e:
        result = f"❌ Error processing '{subject}': {e}"

    for chunk in chunk_text(result):
        await update.message.reply_text(chunk)


def run_bot():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("study", study))
    return app


if __name__ == "__main__":
    app = run_bot()
    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()