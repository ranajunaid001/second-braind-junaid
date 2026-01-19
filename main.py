import os
import json
import threading
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, jsonify

# Environment variables - you set these in Railway
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
GOOGLE_SHEETS_CREDS = os.environ.get("GOOGLE_SHEETS_CREDS")
SHEET_ID = os.environ.get("SHEET_ID")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  # Your chat ID for digest

# Initialize OpenAI client
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Flask app for cron endpoint
flask_app = Flask(__name__)

CLASSIFIER_PROMPT = """You are a classifier for a personal second brain.

Classify the user message into exactly one bucket:
- people (contacts, relationships, follow-ups with people)
- ideas (product ideas, things to build, concepts to explore)
- interviews (job opportunities, leads, applications, interview prep)
- admin (bills, appointments, errands, daily tasks)
- linkedin (content ideas for LinkedIn posts - IMPORTANT: if message contains "draft" or starts with "draft", it's ALWAYS linkedin)

Return JSON ONLY. No markdown. No extra text.

{
  "bucket": "people|ideas|interviews|admin|linkedin",
  "confidence": 0.0-1.0,
  "fields": {}
}

The "fields" object depends on the bucket:

For "people":
{"name": "person's name", "context": "who they are/how you know them", "follow_ups": "next thing to remember or ask"}

For "ideas":
{"idea": "short title", "one_liner": "one sentence description", "notes": "any extra details"}

For "interviews":
{"company": "company name", "role": "job role if mentioned", "status": "Lead|Applied|Scheduled|Completed", "next_step": "what to do next", "date": "date if mentioned or empty"}

For "admin":
{"task": "short title", "status": "Open", "due": "date if mentioned or empty", "next_action": "concrete next step"}

For "linkedin":
{"idea": "post topic or hook", "notes": "the full story or details", "status": "Draft"}

Rules:
- If message contains the word "draft" anywhere, classify as linkedin
- Infer the bucket based on intent
- Extract as much info as possible from the message
- If a field is not mentioned, make a reasonable inference or leave empty
- confidence 0.9+ = very sure, 0.7-0.89 = likely, 0.6-0.69 = weak, <0.6 = uncertain

User message:
"""

def get_sheets_client():
    """Initialize Google Sheets client."""
    creds_dict = json.loads(GOOGLE_SHEETS_CREDS)
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

def classify_message(message: str) -> dict:
    """Send message to ChatGPT for classification."""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": CLASSIFIER_PROMPT + message}
            ],
            temperature=0.3
        )
        result = response.choices[0].message.content.strip()
        return json.loads(result)
    except Exception as e:
        print(f"Classification error: {e}")
        return {
            "bucket": "admin",
            "confidence": 0.3,
            "fields": {"task": message[:50], "status": "Open", "due": "", "next_action": "Review this item"}
        }

def save_to_sheets(captured_text: str, classification: dict, message_id: int) -> bool:
    """Save the classified message to the appropriate Google Sheet tab."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        bucket = classification["bucket"]
        fields = classification["fields"]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Save to the appropriate sheet based on bucket
        if bucket == "people":
            sheet = spreadsheet.worksheet("People")
            row = [
                fields.get("name", ""),
                fields.get("context", ""),
                fields.get("follow_ups", ""),
                timestamp,
                message_id,
                "TRUE"
            ]
        elif bucket == "ideas":
            sheet = spreadsheet.worksheet("Ideas")
            row = [
                fields.get("idea", ""),
                fields.get("one_liner", ""),
                fields.get("notes", ""),
                message_id,
                "TRUE"
            ]
        elif bucket == "interviews":
            sheet = spreadsheet.worksheet("Interviews")
            row = [
                fields.get("company", ""),
                fields.get("role", ""),
                fields.get("status", "Lead"),
                fields.get("next_step", ""),
                fields.get("date", ""),
                message_id,
                "TRUE"
            ]
        elif bucket == "admin":
            sheet = spreadsheet.worksheet("Admin")
            row = [
                fields.get("task", ""),
                fields.get("status", "Open"),
                fields.get("due", ""),
                fields.get("next_action", ""),
                message_id,
                "TRUE"
            ]
        elif bucket == "linkedin":
            sheet = spreadsheet.worksheet("LinkedIn")
            row = [
                fields.get("idea", ""),
                fields.get("notes", ""),
                fields.get("status", "Draft"),
                message_id,
                "TRUE"
            ]
        else:
            # Fallback to Inbox Log
            sheet = spreadsheet.sheet1
            row = [captured_text, bucket, classification["confidence"], timestamp]
        
        sheet.append_row(row)
        
        # Also log to Inbox Log (Sheet1)
        inbox = spreadsheet.sheet1
        title = fields.get("name") or fields.get("idea") or fields.get("company") or fields.get("task") or captured_text[:50]
        inbox.append_row([
            title,
            captured_text,
            bucket.capitalize(),
            classification["confidence"],
            timestamp,
            message_id
        ])
        
        print(f"Saved to {bucket}: {title}")
        return True
    except Exception as e:
        print(f"Sheets error: {e}")
        return False


def fix_entry(message_id: int, new_bucket: str, original_text: str) -> bool:
    """Move an entry from one sheet to another."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        # Find the entry in all sheets by message_id
        sheets_to_check = ["People", "Ideas", "Interviews", "Admin", "LinkedIn"]
        found_sheet = None
        found_row_idx = None
        found_row_data = None
        
        for sheet_name in sheets_to_check:
            try:
                sheet = spreadsheet.worksheet(sheet_name)
                all_values = sheet.get_all_values()
                
                # Find row with matching message_id (second to last column)
                for idx, row in enumerate(all_values[1:], start=2):  # Skip header, 1-indexed
                    if len(row) >= 2:
                        # message_id is second to last column
                        msg_id_col = -2
                        if str(row[msg_id_col]) == str(message_id):
                            found_sheet = sheet_name
                            found_row_idx = idx
                            found_row_data = row
                            break
                if found_sheet:
                    break
            except:
                continue
        
        if not found_sheet:
            print(f"Could not find entry with message_id {message_id}")
            return False
        
        # Mark old entry as inactive
        old_sheet = spreadsheet.worksheet(found_sheet)
        is_active_col = len(found_row_data)  # Last column
        old_sheet.update_cell(found_row_idx, is_active_col, "FALSE")
        
        # Re-classify with forced bucket to get proper fields
        classification = classify_message(original_text)
        classification["bucket"] = new_bucket
        classification["confidence"] = 1.0
        
        # Save to new sheet
        save_to_sheets(original_text, classification, message_id)
        
        print(f"Moved from {found_sheet} to {new_bucket}")
        return True
    except Exception as e:
        print(f"Fix error: {e}")
        return False

def get_top_items(table_name):
    """Get top items from a specific table."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        sheet = spreadsheet.worksheet(table_name)
        rows = sheet.get_all_values()[1:]  # Skip header
        
        items = []
        for row in rows:
            # Check is_active (last column)
            if len(row) >= 2 and row[-1] == "TRUE":
                items.append(row)
        
        return items
    except Exception as e:
        print(f"Error getting top items from {table_name}: {e}")
        return []


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
        # Fallback to simple format
        result = f"Top {table_name}:\n"
        for item in items[:5]:
            result += f"• {item[0]}\n"
        return result


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Classify message and save to Google Sheets."""
    user_message = update.message.text.strip()
    user_message_lower = user_message.lower()
    message_id = update.message.message_id
    
    # Check if this is a "top" command (e.g., "top admin", "top people")
    if user_message_lower.startswith("top"):
        table_request = user_message_lower.replace("top", "").strip()
        
        # Map shortcuts to full names
        table_shortcuts = {
            "ppl": "People",
            "p": "People",
            "people": "People",
            "idea": "Ideas",
            "ideas": "Ideas",
            "i": "Ideas",
            "int": "Interviews",
            "interview": "Interviews",
            "interviews": "Interviews",
            "adm": "Admin",
            "admin": "Admin",
            "a": "Admin",
            "li": "LinkedIn",
            "ln": "LinkedIn",
            "linkedin": "LinkedIn",
            "l": "LinkedIn",
            "all": "all"
        }
        
        table_name = table_shortcuts.get(table_request)
        
        if table_name == "all":
            # Send full digest
            success, message = send_digest_sync()
            if not success:
                await update.message.reply_text("❌ Could not generate digest.")
            return
        elif table_name:
            items = get_top_items(table_name)
            reply = format_top_items(table_name, items)
            await update.message.reply_text(reply)
            return
        else:
            await update.message.reply_text("❌ Unknown table. Use: top people / admin / interviews / ideas / linkedin / all")
            return
    
    # Check if this is a fix command (e.g., "fix admin", "fx admin", "fx ppl")
    if user_message_lower.startswith("fix") or user_message_lower.startswith("fx"):
        # Remove "fix"/"fx" and optional colon, get the bucket
        new_bucket = user_message_lower.replace("fix:", "").replace("fix", "").replace("fx:", "").replace("fx", "").strip()
        
        # Map shortcuts to full names
        bucket_shortcuts = {
            "ppl": "people",
            "p": "people",
            "people": "people",
            "idea": "ideas",
            "ideas": "ideas",
            "i": "ideas",
            "int": "interviews",
            "interview": "interviews",
            "interviews": "interviews",
            "adm": "admin",
            "admin": "admin",
            "a": "admin",
            "li": "linkedin",
            "ln": "linkedin",
            "linkedin": "linkedin",
            "l": "linkedin"
        }
        
        new_bucket = bucket_shortcuts.get(new_bucket, new_bucket)
        
        if new_bucket in ["people", "ideas", "interviews", "admin", "linkedin"]:
            # Get the last saved message for this user
            if "last_message" in context.user_data:
                last = context.user_data["last_message"]
                success = fix_entry(last["message_id"], new_bucket, last["original_text"])
                
                if success:
                    reply = f"✓ Fixed. Moved to {new_bucket.capitalize()}."
                else:
                    reply = "❌ Could not find the entry to fix."
            else:
                reply = "❌ No recent message to fix."
        else:
            reply = "❌ Invalid category. Use: fix people / fix ideas / fix interviews / fix admin / fix linkedin"
        
        await update.message.reply_text(reply)
        return
    
    # Check if this is a correction response for low confidence
    if user_message_lower in ["people", "ideas", "interviews", "admin", "linkedin"]:
        # User is correcting a previous classification
        if "pending_message" in context.user_data:
            pending = context.user_data["pending_message"]
            # Override the bucket with user's choice
            pending["classification"]["bucket"] = user_message_lower
            pending["classification"]["confidence"] = 1.0
            
            success = save_to_sheets(pending["original_text"], pending["classification"], pending["message_id"])
            
            if success:
                fields = pending["classification"]["fields"]
                title = fields.get("name") or fields.get("idea") or fields.get("company") or fields.get("task") or "Item"
                reply = f"✓ Corrected and filed as: {user_message_lower.capitalize()}\nTitle: {title}"
                
                # Store for potential fix later
                context.user_data["last_message"] = {
                    "message_id": pending["message_id"],
                    "original_text": pending["original_text"]
                }
            else:
                reply = "❌ Error saving. Please try again."
            
            del context.user_data["pending_message"]
            await update.message.reply_text(reply)
            return
    
    # Step 1: Classify with ChatGPT
    classification = classify_message(user_message)
    
    # Step 2: Check confidence
    if classification["confidence"] <= 0.6:
        # Store pending message and ask for confirmation
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
    
    # Step 3: Save to Google Sheets (high confidence)
    success = save_to_sheets(user_message, classification, message_id)
    
    # Step 4: Reply to user
    if success:
        confidence_pct = int(classification["confidence"] * 100)
        fields = classification["fields"]
        title = fields.get("name") or fields.get("idea") or fields.get("company") or fields.get("task") or "Item"
        
        reply = f"✓ Filed as: {classification['bucket'].capitalize()}\n"
        reply += f"Title: {title}\n"
        reply += f"Confidence: {confidence_pct}%\n"
        reply += f"Reply 'fix <category>' if wrong."
        
        # Store for potential fix later
        context.user_data["last_message"] = {
            "message_id": message_id,
            "original_text": user_message
        }
    else:
        reply = "❌ Error saving. Please try again."
    
    await update.message.reply_text(reply)

def get_digest_data():
    """Pull data from sheets for daily digest."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        data = {
            "interviews": [],
            "admin": [],
            "people": []
        }
        
        # Get Interviews (active ones)
        try:
            sheet = spreadsheet.worksheet("Interviews")
            rows = sheet.get_all_values()[1:]  # Skip header
            for row in rows:
                if len(row) >= 6 and row[-1] == "TRUE":  # is_active
                    data["interviews"].append({
                        "company": row[0],
                        "role": row[1],
                        "status": row[2],
                        "next_step": row[3],
                        "date": row[4]
                    })
        except Exception as e:
            print(f"Error reading Interviews: {e}")
        
        # Get Admin (open tasks)
        try:
            sheet = spreadsheet.worksheet("Admin")
            rows = sheet.get_all_values()[1:]
            for row in rows:
                if len(row) >= 5 and row[-1] == "TRUE" and row[1] == "Open":
                    data["admin"].append({
                        "task": row[0],
                        "status": row[1],
                        "due": row[2],
                        "next_action": row[3]
                    })
        except Exception as e:
            print(f"Error reading Admin: {e}")
        
        # Get People (with follow-ups)
        try:
            sheet = spreadsheet.worksheet("People")
            rows = sheet.get_all_values()[1:]
            for row in rows:
                if len(row) >= 6 and row[-1] == "TRUE" and row[2]:  # has follow_ups
                    data["people"].append({
                        "name": row[0],
                        "context": row[1],
                        "follow_ups": row[2]
                    })
        except Exception as e:
            print(f"Error reading People: {e}")
        
        return data
    except Exception as e:
        print(f"Error getting digest data: {e}")
        return None


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


async def send_digest_async():
    """Send digest via Telegram."""
    data = get_digest_data()
    if not data:
        return False, "Could not fetch data"
    
    # Check if there's anything to report
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
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(send_digest_async())
    loop.close()
    return result


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
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)


def main():
    missing = []
    if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN")
    if not OPENAI_API_KEY: missing.append("OPENAI_API_KEY")
    if not GOOGLE_SHEETS_CREDS: missing.append("GOOGLE_SHEETS_CREDS")
    if not SHEET_ID: missing.append("SHEET_ID")
    if not TELEGRAM_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
    
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        return
    
    # Start Flask in background thread for cron endpoints
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Flask server started for cron endpoints...")
    
    print("Starting bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is running. Listening for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
