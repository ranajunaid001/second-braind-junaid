import os
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Echo the user message back to confirm bot is working."""
    user_message = update.message.text
    await update.message.reply_text(f"✓ Received: {user_message}")

def main():
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN environment variable not set")
        return
    
    print("Starting bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handle all text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    print("Bot is running. Listening for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
