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
    question_starters = ["who ", "what ", "tell me", "where ", "when ", "how ", "why ", "does ", "is ", "are ", "do "]
    ends_with_question = message.endswith("?")
    starts_with_question = any(message_lower.startswith(q) for q in question_starters)
    
    if ends_with_question or starts_with_question:
        return {"is_question": True, "query": message}
    
    return {"is_question": False}


def extract_search_keywords(query: str) -> list:
    """
    Extract meaningful keywords from a search query.
    Removes common words, keeps important terms.
    """
    # Common words to ignore
    stop_words = {
        "who", "what", "where", "when", "why", "how", "is", "are", "was", "were",
        "do", "does", "did", "the", "a", "an", "at", "in", "on", "to", "for",
        "of", "with", "about", "tell", "me", "i", "my", "you", "your", "we",
        "they", "them", "that", "this", "it", "and", "or", "but", "have", "has",
        "had", "can", "could", "would", "should", "will", "did", "meet", "met",
        "know", "knows", "work", "works", "live", "lives", "any", "some"
    }
    
    # Clean and split
    query_clean = query.lower().strip().rstrip("?").strip()
    words = query_clean.split()
    
    # Filter out stop words, keep meaningful keywords
    keywords = [w for w in words if w not in stop_words and len(w) > 1]
    
    return keywords


def search_people_by_keywords(people_list: list, keywords: list) -> list:
    """
    Search people by keywords. Case-insensitive, partial match.
    Returns list of matching people.
    """
    if not keywords:
        return []
    
    matches = []
    
    for person in people_list:
        # Combine all searchable text
        searchable = " ".join([
            person.get("name", ""),
            person.get("context", ""),
            person.get("notes", ""),
            person.get("follow_ups", "")
        ]).lower()
        
        # Check if ANY keyword matches
        for keyword in keywords:
            if keyword in searchable:
                matches.append(person)
                break  # Don't add same person twice
    
    return matches


def generate_search_answer(question: str, matching_people: list) -> str:
    """
    Use LLM to answer the question based on matching people data.
    """
    if not matching_people:
        return "No one matches that criteria."
    
    # Build context from matching people
    people_info = ""
    for p in matching_people:
        people_info += f"\n- {p['name']}: {p.get('context', '')} | Notes: {p.get('notes', '')[:200]}"
    
    prompt = f"""Answer this question based on the people data below.

Question: "{question}"

Matching people:{people_info}

Rules:
- Answer naturally and concisely
- If multiple people match, list them
- If the data doesn't fully answer the question, say what you know
- Keep it short"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Search answer error: {e}")
        # Fallback: list names
        names = [p["name"] for p in matching_people]
        return f"Found: {', '.join(names)}"


def answer_person_question(person_data: dict, question: str) -> str:
    """
    Use GPT to answer a question about a person based on stored data.
    """
    prompt = f"""Based on this information about {person_data['name']}, answer the question naturally and concisely.

STORED INFORMATION:
Name: {person_data['name']}
Context: {person_data.get('context', 'No context')}
Notes: {person_data.get('notes', 'No notes')}
Follow-ups: {person_data.get('follow_ups', 'None')}
Last updated: {person_data.get('last_touched', 'Unknown')}

QUESTION: {question}

Rules:
- Answer naturally, like a helpful assistant
- If the info doesn't contain the answer, say "I don't have that info about [name]"
- Keep it concise
- Include relevant dates if helpful"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Answer generation error: {e}")
        return f"Sorry, I couldn't process that question about {person_data['name']}."


def search_people_by_criteria(people_list: list, search_query: str) -> list:
    """
    Use GPT to find people matching a search criteria.
    """
    if not people_list:
        return []
    
    people_summary = "\n".join([
        f"- {p['name']}: {p.get('context', '')} | Notes: {p.get('notes', '')[:100]}"
        for p in people_list
    ])
    
    prompt = f"""Given this list of people, find who matches the query.

PEOPLE:
{people_summary}

QUERY: {search_query}

Return JSON array of matching names only. Example: ["John", "Sarah"]
If no matches, return: []"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        result = response.choices[0].message.content.strip()
        return json.loads(result)
    except Exception as e:
        print(f"People search error: {e}")
        return []


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
