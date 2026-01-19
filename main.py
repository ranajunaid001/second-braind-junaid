import os
import json
from datetime import datetime
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials

# Environment variables - you set these in Railway
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
GOOGLE_SHEETS_CREDS = os.environ.get("GOOGLE_SHEETS_CREDS")
SHEET_ID = os.environ.get("SHEET_ID")

# Initialize OpenAI client
openai_client = OpenAI(api_key=OPENAI_API_KEY)

CLASSIFIER_PROMPT = """You are a classifier for a personal second brain.

Classify the user message into exactly one bucket:
- people (contacts, relationships, follow-ups with people)
- ideas (product ideas, things to build, concepts to explore)
- interviews (job opportunities, leads, applications, interview prep)
- admin (bills, appointments, errands, daily tasks)
- linkedin (content ideas for LinkedIn posts)

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
{"idea": "post topic or hook", "notes": "any extra details or angles", "status": "Draft"}

Rules:
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

def save_to_sheets(captured_text: str, classification: dict) -> bool:
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
                timestamp
            ]
        elif bucket == "ideas":
            sheet = spreadsheet.worksheet("Ideas")
            row = [
                fields.get("idea", ""),
                fields.get("one_liner", ""),
                fields.get("notes", "")
            ]
        elif bucket == "interviews":
            sheet = spreadsheet.worksheet("Interviews")
            row = [
                fields.get("company", ""),
                fields.get("role", ""),
                fields.get("status", "Lead"),
                fields.get("next_step", ""),
                fields.get("date", "")
            ]
        elif bucket == "admin":
            sheet = spreadsheet.worksheet("Admin")
            row = [
                fields.get("task", ""),
                fields.get("status", "Open"),
                fields.get("due", ""),
                fields.get("next_action", "")
            ]
        elif bucket == "linkedin":
            sheet = spreadsheet.worksheet("LinkedIn")
            row = [
                fields.get("idea", ""),
                fields.get("notes", ""),
                fields.get("status", "Draft")
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
            timestamp
        ])
        
        print(f"Saved to {bucket}: {title}")
        return True
    except Exception as e:
        print(f"Sheets error: {e}")
        return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Classify message and save to Google Sheets."""
    user_message = update.message.text.strip().lower()
    
    # Check if this is a correction response
    if user_message in ["people", "ideas", "interviews", "admin", "linkedin"]:
        # User is correcting a previous classification
        if "pending_message" in context.user_data:
            pending = context.user_data["pending_message"]
            # Override the bucket with user's choice
            pending["classification"]["bucket"] = user_message
            pending["classification"]["confidence"] = 1.0
            
            success = save_to_sheets(pending["original_text"], pending["classification"])
            
            if success:
                fields = pending["classification"]["fields"]
                title = fields.get("name") or fields.get("idea") or fields.get("company") or fields.get("task") or "Item"
                reply = f"✓ Corrected and filed as: {user_message.capitalize()}\nTitle: {title}"
            else:
                reply = "❌ Error saving. Please try again."
            
            del context.user_data["pending_message"]
            await update.message.reply_text(reply)
            return
    
    user_message = update.message.text  # Get original (non-lowercased) message
    
    # Step 1: Classify with ChatGPT
    classification = classify_message(user_message)
    
    # Step 2: Check confidence
    if classification["confidence"] < 0.6:
        # Store pending message and ask for confirmation
        context.user_data["pending_message"] = {
            "original_text": user_message,
            "classification": classification
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
    success = save_to_sheets(user_message, classification)
    
    # Step 4: Reply to user
    if success:
        confidence_pct = int(classification["confidence"] * 100)
        fields = classification["fields"]
        title = fields.get("name") or fields.get("idea") or fields.get("company") or fields.get("task") or "Item"
        
        reply = f"✓ Filed as: {classification['bucket'].capitalize()}\n"
        reply += f"Title: {title}\n"
        reply += f"Confidence: {confidence_pct}%"
    else:
        reply = "❌ Error saving. Please try again."
    
    await update.message.reply_text(reply)

def main():
    missing = []
    if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN")
    if not OPENAI_API_KEY: missing.append("OPENAI_API_KEY")
    if not GOOGLE_SHEETS_CREDS: missing.append("GOOGLE_SHEETS_CREDS")
    if not SHEET_ID: missing.append("SHEET_ID")
    
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        return
    
    print("Starting bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is running. Listening for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
