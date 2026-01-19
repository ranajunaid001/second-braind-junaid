# =============================================================================
# PROMPTS.PY - All LLM prompts in one place
# Edit this file to improve AI behavior
# =============================================================================

# -----------------------------------------------------------------------------
# CLASSIFIER PROMPT
# Used to classify incoming messages into buckets
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# EXTRACT FIELDS PROMPT
# Used when force rules apply (e.g., "draft" → linkedin)
# -----------------------------------------------------------------------------

def get_extract_fields_prompt(bucket: str, message: str) -> str:
    """Generate prompt to extract fields for a specific bucket."""
    
    field_schemas = {
        "linkedin": '{"idea": "post topic", "notes": "full content", "status": "Draft"}',
        "people": '{"name": "person name", "context": "who they are", "follow_ups": "any action"}',
        "ideas": '{"idea": "short title", "one_liner": "one sentence", "notes": "details"}',
        "interviews": '{"company": "company name", "role": "job role", "status": "Lead", "next_step": "action", "date": ""}',
        "admin": '{"task": "short title", "status": "Open", "due": "", "next_action": "concrete step"}'
    }
    
    schema = field_schemas.get(bucket, '{}')
    
    return f"""Extract fields from this message for the "{bucket}" category.

Return JSON ONLY:
{schema}

Message: {message}"""

# -----------------------------------------------------------------------------
# DIGEST PROMPT
# Used for daily digest - top 3 actions
# -----------------------------------------------------------------------------

DIGEST_PROMPT = """Generate a daily digest. Be extremely concise. No fluff.

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

# -----------------------------------------------------------------------------
# TOP ITEMS PROMPT
# Used for "top people", "top admin", etc.
# -----------------------------------------------------------------------------

def get_top_items_prompt(table_name: str, items: list) -> str:
    """Generate prompt to format top items from a table."""
    import json
    
    return f"""Format these {table_name} items as a short bullet list. Max 5 items.
Each bullet should be one line, actionable if possible.
No headers, no fluff.

Data:
{json.dumps(items[:5])}"""

# -----------------------------------------------------------------------------
# WEEKLY REVIEW PROMPT (for future use)
# -----------------------------------------------------------------------------

WEEKLY_REVIEW_PROMPT = """Write a weekly review under 250 words.

Include:
1) What moved forward this week (2-4 bullets)
2) What is stuck and why (2 bullets)
3) Top 3 priorities for next week (3 bullets)
4) One pattern you notice (1 sentence)

Use only the data provided. Be specific.

Data:
"""

# -----------------------------------------------------------------------------
# MISCLASSIFICATION REPORT PROMPT (for future use)
# -----------------------------------------------------------------------------

MISCLASSIFICATION_PROMPT = """Analyze these classification corrections and identify patterns.

For each pattern, explain:
1. What type of message was misclassified
2. What it was classified as vs what it should have been
3. How to improve the classification

Be concise. Max 100 words.

Corrections data:
"""
