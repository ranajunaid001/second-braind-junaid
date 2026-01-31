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
    """Format person info for display - clean and readable."""
    result = f"👤 {person['name']}\n"
    
    if person.get('context'):
        result += f"{person['context']}\n"
    
    result += "\n"
    
    if person.get('notes'):
        # Parse notes and display as bullet points without dates
        notes = person['notes']
        # Split by bullet separator
        note_list = notes.split(' • ')
        for note in note_list:
            # Remove date prefix like [2026-01-19]
            clean_note = note
            if note.startswith('['):
                clean_note = note.split('] ', 1)[-1]
            result += f"• {clean_note}\n"
    
    if person.get('follow_ups') and not person['follow_ups'].startswith('2026'):
        result += f"\n📌 {person['follow_ups']}\n"
    
    if person.get('last_touched'):
        # Format date nicely
        try:
            date_str = person['last_touched'][:10]  # Get YYYY-MM-DD
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            nice_date = date_obj.strftime("%b %d")  # Jan 19
            result += f"\nLast updated: {nice_date}"
        except:
            pass
    
    return result.strip()
