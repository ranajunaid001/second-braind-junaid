import json
from datetime import datetime
from openai import OpenAI
from config import OPENAI_API_KEY
from prompts import CLASSIFIER_PROMPT, get_extract_fields_prompt

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# =============================================================================
# CLASSIFICATION RULES
# =============================================================================

CONFIDENCE_THRESHOLD = 0.6  # Below this, ask user to confirm

# Keywords that FORCE a category (checked before LLM)
FORCE_RULES = {
    "linkedin": ["draft"],  # If message contains "draft", always linkedin
}


def check_force_rules(message: str) -> str | None:
    """Check if any force rules apply. Returns bucket name or None."""
    message_lower = message.lower()
    for bucket, keywords in FORCE_RULES.items():
        for keyword in keywords:
            if keyword in message_lower:
                return bucket
    return None


def classify(message: str) -> dict:
    """Classify a message into a bucket with confidence score."""
    
    # Check force rules first
    forced_bucket = check_force_rules(message)
    if forced_bucket:
        return {
            "bucket": forced_bucket,
            "confidence": 1.0,
            "fields": extract_fields(message, forced_bucket)
        }
    
    # Call LLM
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": CLASSIFIER_PROMPT + message}
            ],
            temperature=0.3
        )
        result = response.choices[0].message.content.strip()
        return json.loads(result)
    except Exception as e:
        print(f"Classifier error: {e}")
        return {
            "bucket": "admin",
            "confidence": 0.3,
            "fields": {"task": message[:50], "status": "Open", "due": "", "next_action": "Review this item"}
        }


def extract_fields(message: str, bucket: str) -> dict:
    """Extract fields for a forced bucket classification."""
    try:
        prompt = get_extract_fields_prompt(bucket, message)
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return json.loads(response.choices[0].message.content.strip())
    except:
        if bucket == "linkedin":
            return {"idea": message[:50], "notes": message, "status": "Draft"}
        return {}


def needs_confirmation(confidence: float) -> bool:
    """Check if classification needs user confirmation."""
    return confidence <= CONFIDENCE_THRESHOLD


def is_person_question(message: str) -> dict:
    """
    Detect if message is a question about people.
    Simple check - if it looks like a question, return True.
    """
    message_lower = message.lower().strip()
    
    # Question indicators
    question_starters = ["who ", "what ", "tell me", "where ", "when ", "how ", "why ", "does ", "is ", "are ", "do ", "anyone", "anybody"]
    ends_with_question = message.endswith("?")
    starts_with_question = any(message_lower.startswith(q) for q in question_starters)
    
    if ends_with_question or starts_with_question:
        return {"is_question": True, "query": message}
    
    return {"is_question": False}


def answer_people_query(question: str, all_people: list) -> str:
    """
    One LLM call - send all people data and the question.
    LLM figures out everything.
    """
    if not all_people:
        return "I don't have any people saved yet."
    
    # Build all people data
    people_data = ""
    for p in all_people:
        people_data += f"\n---\nName: {p['name']}"
        if p.get('context'):
            people_data += f"\nContext: {p['context']}"
        if p.get('notes'):
            people_data += f"\nNotes: {p['notes'][:300]}"
        if p.get('follow_ups'):
            people_data += f"\nFollow-ups: {p['follow_ups']}"
        if p.get('last_touched'):
            people_data += f"\nLast updated: {p['last_touched']}"
    
    prompt = f"""You are a personal assistant. Here is everyone I know:

{people_data}

Question: "{question}"

Rules:
- Answer naturally and concisely, like a friend would
- Use ALL the data (context, notes, follow-ups) to answer
- If multiple people match, list them all with a number (1. 2. etc)
- If asking about a specific person, give their full info in one clean sentence
- If the data doesn't answer the question, say so honestly
- Keep it short and conversational
- Don't make up information that isn't in the data

FORMATTING RULES (very important):
- NO markdown. No asterisks, no bold, no italic, no headers
- NO dashes or hyphens as separators
- Use commas to separate details
- NO labels like "Name:" or "Context:" or "Notes:"
- NO raw dates like [2026-01-31]
- NO filler like "Let me know if you need more" or "Here's what I found"
- If someone has no follow-ups or notes, just skip it, don't mention it's empty
- For multiple people with same name, use numbered list like:
  Two Sarahs:
  1. Sarah, works at Apple as a designer. Joined a new gym in NH.
  2. Sarah, met at conference. Amazing speaker. Meeting again in NY next month.

Example good answer: Jack, met at residency. Lives in Boston, works at McKinsey.
Example bad answer: **Jack** - Met at residency. Lives in Boston. **Works at:** McKinsey. **Last updated:** Jan 31"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"People query error: {e}")
        return "Sorry, I couldn't process that question."


def semantic_person_match(existing_name: str, existing_context: str, new_name: str, new_context: str) -> str:
    """
    Use GPT to determine if two person entries are the same person.
    
    Returns:
    - "SAME" → definitely same person, auto-merge
    - "LIKELY_SAME" → probably same, ask user
    - "LIKELY_DIFFERENT" → probably different, ask user  
    - "DIFFERENT" → definitely different, save as new
    """
    prompt = f"""You are comparing two entries to determine if they refer to the same person.

EXISTING ENTRY:
Name: {existing_name}
Context: {existing_context}

NEW ENTRY:
Name: {new_name}
Context: {new_context}

Rules:
- If names are clearly different people (e.g., "John" vs "Sarah") → DIFFERENT
- If contexts directly conflict (e.g., "works at Google" vs "works at Apple") → LIKELY_DIFFERENT
- If names match and new context adds info without conflict → LIKELY_SAME
- If names match and contexts are identical or very similar → SAME
- When in doubt, prefer LIKELY_SAME or LIKELY_DIFFERENT (let user decide)

Return ONLY one word: SAME, LIKELY_SAME, LIKELY_DIFFERENT, or DIFFERENT"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        result = response.choices[0].message.content.strip().upper()
        
        # Validate response
        if result in ["SAME", "LIKELY_SAME", "LIKELY_DIFFERENT", "DIFFERENT"]:
            return result
        return "LIKELY_SAME"  # Default to asking user
    except Exception as e:
        print(f"Semantic match error: {e}")
        return "LIKELY_SAME"  # Default to asking user


def format_person_info(person: dict) -> str:
    """Format person info for display - clean, no markdown, conversational."""
    parts = [person['name']]
    
    if person.get('context'):
        parts.append(person['context'])
    
    # Add notes without dates
    if person.get('notes'):
        notes = person['notes']
        note_list = notes.split(' • ')
        for note in note_list:
            clean_note = note
            if note.startswith('['):
                clean_note = note.split('] ', 1)[-1]
            if clean_note and clean_note.lower() not in ' '.join(parts).lower():
                parts.append(clean_note)
    
    if person.get('follow_ups') and not person['follow_ups'].startswith('2026'):
        parts.append("Follow up: " + person['follow_ups'])
    
    return ', '.join(parts[:4])
