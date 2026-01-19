import json
from openai import OpenAI
from config import OPENAI_API_KEY

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# =============================================================================
# CLASSIFICATION RULES - EDIT THIS SECTION TO IMPROVE OUTPUTS
# =============================================================================

CONFIDENCE_THRESHOLD = 0.6  # Below this, ask user to confirm

CATEGORIES = {
    "people": "contacts, relationships, info about specific people, observations, cues, anything about a person",
    "ideas": "product ideas, things to build, concepts to explore",
    "interviews": "job opportunities, leads, applications, interview prep, companies hiring",
    "admin": "bills, appointments, errands, daily tasks, chores",
    "linkedin": "content ideas for LinkedIn posts"
}

# Keywords that FORCE a category (checked before LLM)
FORCE_RULES = {
    "linkedin": ["draft"],  # If message contains "draft", always linkedin
}

# =============================================================================
# CLASSIFIER PROMPT - EDIT THIS TO CHANGE LLM BEHAVIOR
# =============================================================================

CLASSIFIER_PROMPT = """You are a classifier for a personal second brain.

Classify the user message into exactly one bucket:
- people (contacts, relationships, info about specific people - names, facts, observations, cues, anything about a person)
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
{"name": "person's name (REQUIRED - extract from message)", "context": "who they are/how you know them", "follow_ups": "any action item mentioned, or empty"}

For "ideas":
{"idea": "short title", "one_liner": "one sentence description", "notes": "any extra details"}

For "interviews":
{"company": "company name", "role": "job role if mentioned", "status": "Lead|Applied|Scheduled|Completed", "next_step": "what to do next", "date": "date if mentioned or empty"}

For "admin":
{"task": "short title", "status": "Open", "due": "date if mentioned or empty", "next_action": "concrete next step"}

For "linkedin":
{"idea": "post topic or hook", "notes": "the full story or details", "status": "Draft"}

IMPORTANT RULES:
1. If message mentions a person's name + any info about them → ALWAYS "people"
2. If message contains "draft" → ALWAYS "linkedin"
3. "call someone", "follow up with someone" → "people" (it's about the person)
4. "pay bill", "buy groceries", "schedule appointment" → "admin"
5. confidence 0.9+ = very sure, 0.7-0.89 = likely, 0.6-0.69 = weak, <0.6 = uncertain

User message:
"""


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
    # Simple extraction for forced rules - LLM still does the heavy lifting
    try:
        prompt = f"""Extract fields from this message for the "{bucket}" category.

Return JSON ONLY:
"""
        if bucket == "linkedin":
            prompt += '{"idea": "post topic", "notes": "full content", "status": "Draft"}'
        elif bucket == "people":
            prompt += '{"name": "person name", "context": "who they are", "follow_ups": "any action"}'
        
        prompt += f"\n\nMessage: {message}"
        
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
    
    return result.strip()
