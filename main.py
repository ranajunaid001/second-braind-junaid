import json
import threading
import asyncio
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from flask import Flask, jsonify

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, OPENAI_API_KEY
from classifier import classify, needs_confirmation, format_person_info
from memory import save_entry, fix_entry, get_items, get_digest_data, find_person
from openai import OpenAI

# Flask app for cron endpoints
flask_app = Flask(__name__)

# OpenAI client for digest generation
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# =============================================================================
# SHORTCUTS - for fix and top commands
# =============================================================================

BUCKET_SHORTCUTS = {
    "ppl": "people", "p": "people", "people": "people",
    "idea": "ideas", "ideas": "ideas", "i": "ideas",
    "int": "interviews", "interview": "interviews", "interviews": "interviews",
    "adm": "admin", "admin": "admin", "a": "admin",
    "li": "linkedin", "ln": "linkedin", "linkedin": "linkedin", "l": "linkedin"
}

TABLE_SHORTCUTS = {
    "ppl": "People", "p": "People", "people": "People",
    "idea": "Ideas", "ideas": "Ideas", "i": "Ideas",
    "int": "Interviews", "interview": "Interviews", "interviews": "Interviews",
    "adm": "Admin", "admin": "Admin", "a": "Admin",
    "li": "LinkedIn", "ln": "LinkedIn", "linkedin": "LinkedIn", "l": "LinkedIn",
    "all": "all"
}


# =============================================================================
# DIGEST FUNCTIONS
# =============================================================================

def generate_digest(data):
    """Use ChatGPT to generate daily digest."""
    prompt = """Generate a daily digest. Be extremely concise. No fluff.

Rules:
- Max 3 bullet points
- Each bullet = one specific action (verb + what)
- Include company name or person name if relevant
- No greetings, no sign-offs

Example format:
• Follow up with Stripe recruiter about PM role
• Pay electricity bill (due Friday)
• Call mom re: birthday plans

Data:
"""
    prompt += json.dumps(data, indent=2)
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error generating digest: {e}")
        return None


def format_top_items(table_name, items):
    """Format items for Telegram message."""
    if not items:
        return f"No active items in {table_name}."
    
    prompt = f"""Format these {table_name} items as a short bullet list. Max 5 items.
Each bullet should be one line, actionable if possible.
No headers, no fluff.

Data:
{json.dumps(items[:5])}"""
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error formatting items: {e}")
        result = f"Top {table_name}:\n"
        for item in items[:5]:
            result += f"• {item[0]}\n"
        return result


async def send_digest_async():
    """Send digest via Telegram."""
    data = get_digest_data()
    if not data:
        return False, "Could not fetch data"
    
    if not data["interviews"] and not data["admin"] and not data["people"]:
        message = "📋 Daily Digest\n\nNo pending actions. You're all caught up! 🎉"
    else:
        message = generate_digest(data)
        if not message:
            return False, "Could not generate digest"
    
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        return True, "Digest sent"
    except Exception as e:
        print(f"Error sending digest: {e}")
        return False, str(e)


def send_digest_sync():
    """Synchronous wrapper for sending digest."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(send_digest_async())
    loop.close()
    return result


# =============================================================================
# FLASK ENDPOINTS
# =============================================================================

@flask_app.route("/digest", methods=["GET", "POST"])
def digest_endpoint():
    """Endpoint for cron job to trigger daily digest."""
    success, message = send_digest_sync()
    if success:
        return jsonify({"status": "ok", "message": message}), 200
    else:
        return jsonify({"status": "error", "message": message}), 500


@flask_app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "ok"}), 200


def run_flask():
    """Run Flask in a separate thread."""
    import os
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)


# =============================================================================
# TELEGRAM MESSAGE HANDLER
# =============================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main message handler - routes all incoming messages."""
    user_message = update.message.text.strip()
    user_message_lower = user_message.lower()
    message_id = update.message.message_id
    
    # ----- COMMAND: who <name> -----
    if user_message_lower.startswith("who "):
        name = user_message[4:].strip()
        matches = find_person(name)
        
        if not matches:
            reply = f"No one found matching '{name}'."
        elif len(matches) == 1:
            reply = format_person_info(matches[0])
        else:
            reply = f"Found {len(matches)} people:\n\n"
            for m in matches:
                reply += f"• {m['name']}"
                if m.get('context'):
                    reply += f" ({m['context'][:30]}...)"
                reply += "\n"
            reply += "\nBe more specific."
        
        await update.message.reply_text(reply)
        return
    
    # ----- COMMAND: top <table> -----
    if user_message_lower.startswith("top"):
        table_request = user_message_lower.replace("top", "").strip()
        table_name = TABLE_SHORTCUTS.get(table_request)
        
        if table_name == "all":
            success, message = send_digest_sync()
            if not success:
                await update.message.reply_text("❌ Could not generate digest.")
            return
        elif table_name:
            items = get_items(table_name)
            reply = format_top_items(table_name, items)
            await update.message.reply_text(reply)
            return
        else:
            await update.message.reply_text("❌ Unknown table. Use: top people / admin / interviews / ideas / linkedin / all")
            return
    
    # ----- COMMAND: fix <bucket> -----
    if user_message_lower.startswith("fix") or user_message_lower.startswith("fx"):
        new_bucket = user_message_lower.replace("fix:", "").replace("fix", "").replace("fx:", "").replace("fx", "").strip()
        new_bucket = BUCKET_SHORTCUTS.get(new_bucket, new_bucket)
        
        if new_bucket in ["people", "ideas", "interviews", "admin", "linkedin"]:
            if "last_message" in context.user_data:
                last = context.user_data["last_message"]
                success, old_bucket = fix_entry(
                    last["message_id"], 
                    new_bucket, 
                    last["original_text"],
                    last["classification"]
                )
                
                if success:
                    reply = f"✓ Fixed. Moved from {old_bucket} to {new_bucket.capitalize()}."
                else:
                    reply = "❌ Could not find the entry to fix."
            else:
                reply = "❌ No recent message to fix."
        else:
            reply = "❌ Invalid category. Use: fix people / fix ideas / fix interviews / fix admin / fix linkedin"
        
        await update.message.reply_text(reply)
        return
    
    # ----- LOW CONFIDENCE CORRECTION -----
    if user_message_lower in ["people", "ideas", "interviews", "admin", "linkedin"]:
        if "pending_message" in context.user_data:
            pending = context.user_data["pending_message"]
            pending["classification"]["bucket"] = user_message_lower
            pending["classification"]["confidence"] = 1.0
            
            success = save_entry(pending["original_text"], pending["classification"], pending["message_id"])
            
            if success:
                fields = pending["classification"]["fields"]
                title = fields.get("name") or fields.get("idea") or fields.get("company") or fields.get("task") or "Item"
                reply = f"✓ Corrected and filed as: {user_message_lower.capitalize()}\nTitle: {title}"
                
                context.user_data["last_message"] = {
                    "message_id": pending["message_id"],
                    "original_text": pending["original_text"],
                    "classification": pending["classification"]
                }
            else:
                reply = "❌ Error saving. Please try again."
            
            del context.user_data["pending_message"]
            await update.message.reply_text(reply)
            return
    
    # ----- NORMAL MESSAGE: CLASSIFY AND SAVE -----
    classification = classify(user_message)
    
    # Check if needs confirmation
    if needs_confirmation(classification["confidence"]):
        context.user_data["pending_message"] = {
            "original_text": user_message,
            "classification": classification,
            "message_id": message_id
        }
        
        fields = classification["fields"]
        title = fields.get("name") or fields.get("idea") or fields.get("company") or fields.get("task") or user_message[:30]
        
        reply = f"🤔 Not sure about this one.\n\n"
        reply += f"Message: \"{user_message[:50]}{'...' if len(user_message) > 50 else ''}\"\n"
        reply += f"My guess: {classification['bucket'].capitalize()} ({int(classification['confidence'] * 100)}%)\n\n"
        reply += "Reply with the correct category:\n"
        reply += "• people\n• ideas\n• interviews\n• admin\n• linkedin"
        
        await update.message.reply_text(reply)
        return
    
    # Save to memory
    success = save_entry(user_message, classification, message_id)
    
    if success:
        confidence_pct = int(classification["confidence"] * 100)
        fields = classification["fields"]
        title = fields.get("name") or fields.get("idea") or fields.get("company") or fields.get("task") or "Item"
        
        reply = f"✓ Filed as: {classification['bucket'].capitalize()}\n"
        reply += f"Title: {title}\n"
        reply += f"Confidence: {confidence_pct}%\n"
        reply += f"Reply 'fix <category>' if wrong."
        
        context.user_data["last_message"] = {
            "message_id": message_id,
            "original_text": user_message,
            "classification": classification
        }
    else:
        reply = "❌ Error saving. Please try again."
    
    await update.message.reply_text(reply)


# =============================================================================
# MAIN
# =============================================================================

def main():
    import os
    
    # Check required env vars
    missing = []
    if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN")
    if not os.environ.get("OPENAI_API_KEY"): missing.append("OPENAI_API_KEY")
    if not os.environ.get("GOOGLE_SHEETS_CREDS"): missing.append("GOOGLE_SHEETS_CREDS")
    if not os.environ.get("SHEET_ID"): missing.append("SHEET_ID")
    if not TELEGRAM_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
    
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        return
    
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Flask server started for cron endpoints...")
    
    # Start Telegram bot
    print("Starting bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is running. Listening for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
