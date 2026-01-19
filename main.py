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
- projects (tasks, goals, things to do)
- ideas (thoughts, concepts, things to explore)
- admin (bills, appointments, logistics, chores)

Return JSON ONLY. No markdown. No extra text.

{
  "bucket": "people|projects|ideas|admin",
  "title": "short descriptive title",
  "confidence": 0.0-1.0
}

Rules:
- confidence 0.9+ = very sure, 0.7-0.89 = likely, 0.6-0.69 = weak, <0.6 = uncertain
- title should be specific and human readable
- If unsure, still pick best guess but lower confidence

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
            "title": "NEEDS REVIEW: " + message[:50],
            "confidence": 0.3
        }

def save_to_sheets(captured_text: str, classification: dict) -> bool:
    """Save the classified message to Google Sheets."""
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SHEET_ID).sheet1
        
        row = [
            classification["title"],
            captured_text,
            classification["bucket"].capitalize(),
            classification["confidence"],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]
        
        sheet.append_row(row)
        print(f"Saved to sheets: {classification['title']}")
        return True
    except Exception as e:
        print(f"Sheets error: {e}")
        return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Classify message and save to Google Sheets."""
    user_message = update.message.text
    
    # Step 1: Classify with ChatGPT
    classification = classify_message(user_message)
    
    # Step 2: Save to Google Sheets
    success = save_to_sheets(user_message, classification)
    
    # Step 3: Reply to user
    if success:
        confidence_pct = int(classification["confidence"] * 100)
        reply = f"✓ Filed as: {classification['bucket'].capitalize()}\n"
        reply += f"Title: {classification['title']}\n"
        reply += f"Confidence: {confidence_pct}%"
        
        if classification["confidence"] < 0.6:
            reply += "\n\n⚠️ Low confidence. Reply with: people / projects / ideas / admin to correct."
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
