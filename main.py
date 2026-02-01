import json
import threading
import asyncio
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from flask import Flask, jsonify

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, OPENAI_API_KEY
from classifier import classify, needs_confirmation, format_person_info, semantic_person_match, is_person_question, answer_people_query, answer_actionable_query
from memory import save_entry, fix_entry, get_items, get_digest_data, find_similar_person, append_to_person, extract_identifier, get_all_people
from prompts import DIGEST_PROMPT, get_top_items_prompt
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
    "things": "things", "thing": "things", "t": "things",
    "li": "linkedin", "ln": "linkedin", "linkedin": "linkedin", "l": "linkedin"
}

TABLE_SHORTCUTS = {
    "ppl": "People", "p": "People", "people": "People",
    "idea": "Ideas", "ideas": "Ideas", "i": "Ideas",
    "int": "Interviews", "interview": "Interviews", "interviews": "Interviews",
    "things": "Things", "thing": "Things", "t": "Things",
    "li": "LinkedIn", "ln": "LinkedIn", "linkedin": "LinkedIn", "l": "LinkedIn",
    "all": "all"
}


# =============================================================================
# CONFIRMATION PARSING
# =============================================================================

AFFIRMATIVE = [
    # English basics
    "y", "yes", "yea", "yeah", "yep", "yup", "ya", "ye", "ys",
    # Casual
    "mhm", "mm", "mmhm", "uh huh", "uhuh", "sure", "ok", "okay", "k", "kk",
    # Confirming
    "correct", "right", "exactly", "indeed", "absolutely", "definitely", "certainly", "totally", "for sure", "of course",
    # That's the one
    "that's him", "thats him", "that's her", "thats her", "that's them", "thats them",
    "that's the one", "thats the one", "the one", "that one", "this one",
    "him", "her", "them", "same", "same one", "same person", "same guy", "same dude",
    # Bingo
    "bingo", "yessir", "yes sir", "yesss", "yass", "yasss", "yaaas",
    # Affirmative slang
    "bet", "word", "facts", "true", "tru", "aight", "ight", "fosho", "fo sho",
    # Short confirms
    "si", "oui", "ja", "hai", "da",
    # Emojis
    "👍", "✅", "👌", "🙌", "💯", "✔", "☑",
    # Typos
    "yse", "yess", "yea h", "yeap", "yep!", "yes!", "ya!", "yeah!"
]

NEGATIVE = [
    # English basics
    "n", "no", "nah", "nope", "nop", "na", "nay",
    # Casual
    "not really", "not him", "not her", "not them", "not that one",
    "wrong", "wrong one", "wrong person", "wrong guy",
    # Different
    "different", "different one", "different person", "different guy",
    "another", "another one", "another person", "another guy",
    # New
    "new", "new one", "new person", "create new", "make new", "add new",
    # Nope variations
    "nuh uh", "nuhuh", "nu uh", "negative", "negatory",
    # Slang
    "cap", "nada", "no way", "hell no", "heck no",
    # Dismissive
    "nothing", "nevermind", "never mind", "forget it", "skip", "none",
    # Emojis
    "👎", "❌", "✖", "🚫",
    # Typos
    "ni", "np", "noo", "nooo", "nope!"
]


def parse_confirmation(reply: str) -> str:
    """Parse user reply to merge confirmation. Returns CONFIRM, DENY, or OTHER."""
    reply = reply.lower().strip()
    
    # Check exact matches first
    if reply in AFFIRMATIVE:
        return "CONFIRM"
    if reply in NEGATIVE:
        return "DENY"
    
    # Check if any affirmative phrase is in the reply
    for phrase in AFFIRMATIVE:
        if len(phrase) > 2 and phrase in reply:  # Only check phrases, not single chars
            return "CONFIRM"
    
    # Check if any negative phrase is in the reply
    for phrase in NEGATIVE:
        if len(phrase) > 2 and phrase in reply:
            return "DENY"
    
    return "OTHER"


# =============================================================================
# DIGEST FUNCTIONS
# =============================================================================

def generate_digest(data):
    """Use ChatGPT to generate daily digest."""
    prompt = DIGEST_PROMPT + json.dumps(data, indent=2)
    
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
    
    prompt = get_top_items_prompt(table_name, items)
    
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
    
    if not data["interviews"] and not data["things"] and not data["people"]:
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
    
    
    # ----- PERSON QUESTION HANDLER (pending answer) -----
    if "pending_person_question" in context.user_data:
        pending = context.user_data["pending_person_question"]
        
        # Check if user selected a number
        if user_message.strip().isdigit():
            idx = int(user_message.strip()) - 1
            if 0 <= idx < len(pending["matches"]):
                selected = pending["matches"][idx]
                answer = answer_people_query(pending["original_question"], [selected])
                del context.user_data["pending_person_question"]
                await update.message.reply_text(answer)
                return
            else:
                await update.message.reply_text(f"Pick a number between 1 and {len(pending['matches'])}")
                return
        
        # Check if user confirmed (yes/no for single match)
        confirmation = parse_confirmation(user_message)
        if confirmation == "CONFIRM" and len(pending["matches"]) == 1:
            selected = pending["matches"][0]
            answer = answer_people_query(pending["original_question"], [selected])
            del context.user_data["pending_person_question"]
            await update.message.reply_text(answer)
            return
        elif confirmation == "DENY":
            del context.user_data["pending_person_question"]
            await update.message.reply_text("Ok, nevermind.")
            return
        
        # Check if user mentioned an identifier to pick
        user_lower = user_message.lower()
        for i, match in enumerate(pending["matches"]):
            identifier = extract_identifier(match.get("context", ""))
            if identifier:
                id_words = identifier.lower().replace("from ", "").replace("your ", "").split()
                if any(word in user_lower for word in id_words if len(word) > 2):
                    selected = match
                    answer = answer_people_query(pending["original_question"], [selected])
                    del context.user_data["pending_person_question"]
                    await update.message.reply_text(answer)
                    return
        
        # Didn't understand - clear and fall through to process as new message
        del context.user_data["pending_person_question"]
    
    # ----- ACTIONABLE TASKS QUERY -----
    action_phrases = [
        # today
        "items for today", "give me top things for today",
        "what do i need to do today", "what's due today", "whats due today",
        "today's tasks", "todays tasks", "what's on my plate", "whats on my plate",
        "what should i do today", "what do i have today", "anything pending",
        "my tasks", "my actions", "what's pending", "whats pending",
        "what do i need to do", "what should i do", "what should i focus on",
        "anything for today", "tasks for today", "what's up for today",
        "whats up for today", "priorities", "priorities for today",
        "what's coming up", "whats coming up", "anything due today",
        "what's happening today", "whats happening today", "to do list",
        "todo list", "todos", "to dos",
        # this week
        "tasks for this week", "this week's tasks", "this weeks tasks",
        "what's due this week", "whats due this week", "anything this week",
        "what do i have this week", "priorities this week", "what's coming up this week",
        "whats coming up this week", "anything pending this week",
        "weekly tasks", "week ahead", "the week ahead"
    ]
    
    if user_message_lower in action_phrases or user_message_lower.rstrip("?") in action_phrases:
        from memory import get_actionable_data
        data = get_actionable_data()
        if not data:
            await update.message.reply_text("Could not fetch data.")
            return
        
        reply = answer_actionable_query(user_message, data)
        await update.message.reply_text(reply)
        return

    # ----- DETECT PERSON QUESTION -----
    question_check = is_person_question(user_message)
    print(f"[DEBUG] Question check result: {question_check}")
    
    if question_check.get("is_question"):
        query = question_check.get("query", user_message)
        print(f"[DEBUG] Query: {query}")
        
        # Get all people
        all_people = get_all_people()
        print(f"[DEBUG] Total people found: {len(all_people) if all_people else 0}")
        
        if not all_people:
            await update.message.reply_text("I don't have any people saved yet.")
            return
        
        # Check if question is about a specific person name with multiple matches
        # Extract potential name from question (strip question words)
        query_lower = query.lower().strip().rstrip('?!.')
        strip_words = ["tell me about", "tell about", "what about", "who is", "who's", "show me"]
        extracted_name = query_lower
        for sw in strip_words:
            if extracted_name.startswith(sw):
                extracted_name = extracted_name[len(sw):].strip()
                break
        
        # Check if extracted name matches multiple people
        if extracted_name:
            name_matches = [p for p in all_people if p['name'].lower() == extracted_name.lower()]
            if len(name_matches) > 1:
                # Multiple matches — ask which one (question mode)
                context.user_data["pending_person_question"] = {
                    "original_question": query,
                    "matches": name_matches
                }
                
                person_name = name_matches[0]['name']
                reply = f"Which {person_name}?\n"
                for i, m in enumerate(name_matches, 1):
                    identifier = extract_identifier(m.get("context", ""))
                    reply_line = f"{i}. {m['name']}"
                    if identifier:
                        reply_line += f", {identifier.replace('from ', '')}"
                    reply += reply_line + "\n"
                
                await update.message.reply_text(reply)
                return
        
        # Single or no specific name — send all data to LLM
        answer = answer_people_query(query, all_people)
        print(f"[DEBUG] LLM answer: {answer}")
        await update.message.reply_text(answer)
        return

    if user_message_lower.startswith("top"):
        table_request = user_message_lower.replace("top", "").strip()
        table_name = TABLE_SHORTCUTS.get(table_request)
        
        if table_name == "all":
            # Send full digest directly (async)
            data = get_digest_data()
            if not data:
                await update.message.reply_text("❌ Could not fetch data.")
                return
            
            if not data["interviews"] and not data["things"] and not data["people"]:
                reply = "📋 Daily Digest\n\nNo pending actions. You're all caught up! 🎉"
            else:
                reply = generate_digest(data)
                if not reply:
                    await update.message.reply_text("❌ Could not generate digest.")
                    return
            
            await update.message.reply_text(reply)
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
        
        if new_bucket in ["people", "ideas", "interviews", "things", "linkedin"]:
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
            reply = "❌ Invalid category. Use: fix people / fix ideas / fix interviews / fix things / fix linkedin"
        
        await update.message.reply_text(reply)
        return
    
    # ----- LOW CONFIDENCE CORRECTION -----
    if user_message_lower in ["people", "ideas", "interviews", "things", "linkedin"]:
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
    
    # ----- MERGE CONFIRMATION -----
    if "pending_merge" in context.user_data:
        pending = context.user_data["pending_merge"]
        all_matches = pending.get("all_matches", [])
        
        # Check if user selected a number (for multiple matches)
        if user_message.strip().isdigit():
            idx = int(user_message.strip()) - 1
            if 0 <= idx < len(all_matches):
                selected = all_matches[idx]
                success = append_to_person(
                    selected["row_idx"],
                    pending["original_text"],
                    pending["classification"]["fields"],
                    pending["message_id"]
                )
                if success:
                    reply = f"Added to {selected['name']}."
                else:
                    reply = "❌ Error updating. Please try again."
                del context.user_data["pending_merge"]
                await update.message.reply_text(reply)
                return
            else:
                await update.message.reply_text(f"Pick a number between 1 and {len(all_matches)}")
                return
        
        # Check if user typed an identifier to select (e.g., "google", "mckinsey")
        user_lower = user_message.lower()
        for match in all_matches:
            identifier = extract_identifier(match.get("context", ""))
            if identifier:
                id_words = identifier.lower().replace("from ", "").replace("your ", "").split()
                if any(word in user_lower for word in id_words if len(word) > 2):
                    success = append_to_person(
                        match["row_idx"],
                        pending["original_text"],
                        pending["classification"]["fields"],
                        pending["message_id"]
                    )
                    if success:
                        reply = f"Added to {match['name']}."
                    else:
                        reply = "❌ Error updating. Please try again."
                    del context.user_data["pending_merge"]
                    await update.message.reply_text(reply)
                    return
        
        confirmation = parse_confirmation(user_message)
        
        if confirmation == "CONFIRM":
            # Single match confirmation
            if pending.get("existing_person"):
                success = append_to_person(
                    pending["existing_person"]["row_idx"],
                    pending["original_text"],
                    pending["classification"]["fields"],
                    pending["message_id"]
                )
                
                if success:
                    reply = f"Added to {pending['existing_person']['name']}."
                else:
                    reply = "❌ Error updating. Please try again."
            elif len(all_matches) == 1:
                success = append_to_person(
                    all_matches[0]["row_idx"],
                    pending["original_text"],
                    pending["classification"]["fields"],
                    pending["message_id"]
                )
                if success:
                    reply = f"Added to {all_matches[0]['name']}."
                else:
                    reply = "❌ Error updating. Please try again."
            else:
                reply = "Please pick a number or type an identifier."
                await update.message.reply_text(reply)
                return
            
            del context.user_data["pending_merge"]
            await update.message.reply_text(reply)
            return
        
        elif confirmation == "DENY":
            # Save as new person
            success = save_entry(pending["original_text"], pending["classification"], pending["message_id"], force_new=True)
            
            if success:
                name = pending["classification"]["fields"].get("name", "")
                reply = f"Got it, new {name} saved."
            else:
                reply = "❌ Error saving. Please try again."
            
            del context.user_data["pending_merge"]
            await update.message.reply_text(reply)
            return
        
        else:
            # OTHER - user sent something unrelated, clear pending and process as new message
            del context.user_data["pending_merge"]
            # Fall through to normal message processing
    
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
        reply += "• people\n• ideas\n• interviews\n• things\n• linkedin"
        
        await update.message.reply_text(reply)
        return
    
    # ----- CHECK FOR SIMILAR PERSON (MERGE PROMPT) -----
    if classification["bucket"] == "people":
        name = classification["fields"].get("name", "")
        new_context = classification["fields"].get("context", "")
        
        if name:
            similar_matches = find_similar_person(name)
            
            if similar_matches:
                if len(similar_matches) == 1:
                    # Single match — ask to confirm
                    similar = similar_matches[0]
                    identifier = extract_identifier(similar["context"])
                    
                    context.user_data["pending_merge"] = {
                        "original_text": user_message,
                        "classification": classification,
                        "message_id": message_id,
                        "existing_person": similar,
                        "all_matches": similar_matches
                    }
                    
                    # Clean format: "Same Alex? (works at Google)"
                    if identifier:
                        reply = f"Same {similar['name']}? ({identifier.replace('from ', '')})"
                    else:
                        reply = f"Same {similar['name']}?"
                    
                    await update.message.reply_text(reply)
                    return
                
                else:
                    # Multiple matches — ask which one
                    context.user_data["pending_merge"] = {
                        "original_text": user_message,
                        "classification": classification,
                        "message_id": message_id,
                        "existing_person": None,
                        "all_matches": similar_matches
                    }
                    
                    reply = "Which one?\n"
                    for i, m in enumerate(similar_matches, 1):
                        identifier = extract_identifier(m.get("context", ""))
                        reply += f"{i}. {m['name']}"
                        if identifier:
                            reply += f", {identifier.replace('from ', '')}"
                        reply += "\n"
                    reply += f"\nOr say 'new' to create a new {name}."
                    
                    await update.message.reply_text(reply)
                    return
    
    # Save to memory
    success = save_entry(user_message, classification, message_id)
    
    if success:
        confidence_pct = int(classification["confidence"] * 100)
        fields = classification["fields"]
        bucket = classification['bucket'].capitalize()
        title = fields.get("name") or fields.get("idea") or fields.get("company") or fields.get("task") or "Item"
        
        # Build conversational reply
        reply = f"Got it, saved to {bucket}.\n\n"
        
        # Format based on bucket
        if classification['bucket'] == 'people':
            reply += f"{title}"
            if fields.get('context'):
                reply += f", {fields.get('context')}"
        elif classification['bucket'] == 'things':
            reply += f"{title}"
            if fields.get('due'):
                reply += f", due: {fields.get('due')}"
        elif classification['bucket'] == 'ideas':
            reply += f"{title}"
            if fields.get('one_liner'):
                reply += f", {fields.get('one_liner')}"
        elif classification['bucket'] == 'interviews':
            reply += f"{title}"
            if fields.get('role'):
                reply += f", {fields.get('role')}"
        else:
            reply += f"{title}"
        
        reply += f"\n\nWrong? Just say: ideas, things, etc."
        
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
