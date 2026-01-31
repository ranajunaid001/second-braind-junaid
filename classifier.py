import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_SHEETS_CREDS, SHEET_ID

def get_sheets_client():
    """Initialize Google Sheets client."""
    creds_dict = json.loads(GOOGLE_SHEETS_CREDS)
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)


def extract_identifier(context: str) -> str:
    """Extract the most identifying detail from context."""
    if not context:
        return ""
    
    context_lower = context.lower()
    
    # Check for company/workplace
    work_keywords = ["works at", "at ", "from ", "@ "]
    for keyword in work_keywords:
        if keyword in context_lower:
            idx = context_lower.find(keyword)
            rest = context[idx + len(keyword):].strip()
            # Get first word or two (company name)
            words = rest.split()
            if words:
                company = words[0].rstrip('.,;:')
                return f"from {company}"
    
    # Check for relationship
    relation_keywords = ["roommate", "friend", "colleague", "brother", "sister", "wife", "husband", "partner", "boss", "manager", "coworker"]
    for rel in relation_keywords:
        if rel in context_lower:
            return f"your {rel}"
    
    # Check for location
    location_keywords = ["lives in", "in ", "based in"]
    for keyword in location_keywords:
        if keyword in context_lower:
            idx = context_lower.find(keyword)
            rest = context[idx + len(keyword):].strip()
            words = rest.split()
            if words:
                location = words[0].rstrip('.,;:')
                return f"in {location}"
    
    # Check for event/meeting context
    event_keywords = ["met at", "from the", "at the"]
    for keyword in event_keywords:
        if keyword in context_lower:
            idx = context_lower.find(keyword)
            rest = context[idx + len(keyword):].strip()
            words = rest.split()[:3]
            if words:
                event = " ".join(words).rstrip('.,;:')
                return f"from the {event}"
    
    # Fallback: first few words of context
    words = context.split()[:3]
    if words:
        return " ".join(words).rstrip('.,;:')
    
    return ""


def fuzzy_match_name(name1: str, name2: str) -> float:
    """Calculate similarity between two names. Returns 0.0 to 1.0."""
    n1 = name1.lower().strip()
    n2 = name2.lower().strip()
    
    # Exact match
    if n1 == n2:
        return 1.0
    
    # One contains the other (e.g., "John" in "John Smith")
    if n1 in n2 or n2 in n1:
        return 0.9
    
    # First name match
    n1_first = n1.split()[0] if n1.split() else n1
    n2_first = n2.split()[0] if n2.split() else n2
    if n1_first == n2_first:
        return 0.85
    
    # Simple character-based similarity
    shorter = min(len(n1), len(n2))
    longer = max(len(n1), len(n2))
    if longer == 0:
        return 0.0
    
    matches = sum(c1 == c2 for c1, c2 in zip(n1, n2))
    return matches / longer


def find_similar_person(name: str) -> dict:
    """Find a person with similar name. Returns best match or None."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        sheet = spreadsheet.worksheet("People")
        all_rows = sheet.get_all_values()
        
        name_lower = name.lower().strip()
        best_match = None
        best_score = 0.0
        
        for idx, row in enumerate(all_rows[1:], start=2):
            if len(row) >= 1 and row[-1] == "TRUE":
                existing_name = row[0]
                score = fuzzy_match_name(name, existing_name)
                
                if score > best_score and score >= 0.8:
                    best_score = score
                    best_match = {
                        "row_idx": idx,
                        "name": existing_name,
                        "context": row[1] if len(row) > 1 else "",
                        "notes": row[2] if len(row) > 2 else "",
                        "follow_ups": row[3] if len(row) > 3 else "",
                        "last_touched": row[4] if len(row) > 4 else "",
                        "score": score
                    }
        
        return best_match
    except Exception as e:
        print(f"Memory error (find similar): {e}")
        return None


def append_to_person(row_idx: int, new_text: str, fields: dict, message_id: int) -> bool:
    """Append new note to existing person."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        sheet = spreadsheet.worksheet("People")
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        note_entry = f"[{timestamp[:10]}] {new_text}"
        
        # Get current row
        all_rows = sheet.get_all_values()
        current_row = all_rows[row_idx - 1]
        
        # Append to notes
        current_notes = current_row[2] if len(current_row) > 2 else ""
        new_notes = current_notes + " • " + note_entry if current_notes else note_entry
        
        # Update notes (column 3), last touched (column 5), message_id (column 6)
        sheet.update_cell(row_idx, 3, new_notes)
        sheet.update_cell(row_idx, 5, timestamp)
        sheet.update_cell(row_idx, 6, message_id)
        
        # Update follow-ups if provided
        if fields.get("follow_ups"):
            current_followups = current_row[3] if len(current_row) > 3 else ""
            new_followups = current_followups + " | " + fields.get("follow_ups") if current_followups else fields.get("follow_ups")
            sheet.update_cell(row_idx, 4, new_followups)
        
        # Update context if new info provided
        if fields.get("context"):
            current_context = current_row[1] if len(current_row) > 1 else ""
            if fields.get("context").lower() not in current_context.lower():
                new_context = current_context + ", " + fields.get("context") if current_context else fields.get("context")
                sheet.update_cell(row_idx, 2, new_context)
        
        print(f"Appended to existing person at row {row_idx}")
        return True
    except Exception as e:
        print(f"Memory error (append person): {e}")
        return False


def save_entry(captured_text: str, classification: dict, message_id: int, force_new: bool = False) -> bool:
    """Save the classified message to the appropriate Google Sheet tab."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        bucket = classification["bucket"]
        fields = classification["fields"]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Save to the appropriate sheet based on bucket
        if bucket == "people":
            # Check if person already exists (unless force_new)
            saved = save_or_update_person(spreadsheet, fields, captured_text, message_id, timestamp, force_new)
            if saved:
                log_to_inbox(spreadsheet, fields.get("name", ""), captured_text, bucket, classification["confidence"], timestamp, message_id)
            return saved
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
        elif bucket == "things":
            sheet = spreadsheet.worksheet("Things")
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
            return False
        
        sheet.append_row(row)
        
        # Log to Inbox Log
        title = fields.get("name") or fields.get("idea") or fields.get("company") or fields.get("task") or captured_text[:50]
        log_to_inbox(spreadsheet, title, captured_text, bucket, classification["confidence"], timestamp, message_id)
        
        print(f"Saved to {bucket}: {title}")
        return True
    except Exception as e:
        print(f"Memory error (save): {e}")
        return False


def save_or_update_person(spreadsheet, fields, captured_text, message_id, timestamp, force_new: bool = False) -> bool:
    """Save new person or update existing person's notes."""
    try:
        sheet = spreadsheet.worksheet("People")
        all_rows = sheet.get_all_values()
        
        name = fields.get("name", "").strip()
        if not name:
            return False
        
        # Search for existing person (exact match, case-insensitive) unless force_new
        found_idx = None
        if not force_new:
            for idx, row in enumerate(all_rows[1:], start=2):  # Skip header
                if len(row) >= 1 and row[0].lower() == name.lower() and row[-1] == "TRUE":
                    found_idx = idx
                    break
        
        note_entry = f"[{timestamp[:10]}] {captured_text}"
        
        if found_idx:
            # Person exists - append to notes
            current_notes = all_rows[found_idx - 1][2] if len(all_rows[found_idx - 1]) > 2 else ""
            new_notes = current_notes + " • " + note_entry if current_notes else note_entry
            
            # Update notes (column 3), last touched (column 5), message_id (column 6)
            sheet.update_cell(found_idx, 3, new_notes)
            sheet.update_cell(found_idx, 5, timestamp)
            sheet.update_cell(found_idx, 6, message_id)
            
            # Update follow-ups if provided
            if fields.get("follow_ups"):
                sheet.update_cell(found_idx, 4, fields.get("follow_ups"))
            
            print(f"Updated existing person: {name}")
        else:
            # New person - create row
            row = [
                name,
                fields.get("context", ""),
                note_entry,
                fields.get("follow_ups", ""),
                timestamp,
                message_id,
                "TRUE"
            ]
            sheet.append_row(row)
            print(f"Created new person: {name}")
        
        return True
    except Exception as e:
        print(f"Memory error (person): {e}")
        return False


def find_person(name: str) -> list:
    """Find person(s) by name. Returns list of matches."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        sheet = spreadsheet.worksheet("People")
        all_rows = sheet.get_all_values()
        
        matches = []
        name_lower = name.lower()
        
        for idx, row in enumerate(all_rows[1:], start=2):
            if len(row) >= 1 and row[-1] == "TRUE":
                if name_lower in row[0].lower():
                    matches.append({
                        "row_idx": idx,
                        "name": row[0],
                        "context": row[1] if len(row) > 1 else "",
                        "notes": row[2] if len(row) > 2 else "",
                        "follow_ups": row[3] if len(row) > 3 else "",
                        "last_touched": row[4] if len(row) > 4 else ""
                    })
        
        return matches
    except Exception as e:
        print(f"Memory error (find person): {e}")
        return []


def get_all_people() -> list:
    """Get all active people from the sheet."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        sheet = spreadsheet.worksheet("People")
        all_rows = sheet.get_all_values()
        
        people = []
        for idx, row in enumerate(all_rows[1:], start=2):
            if len(row) >= 1 and row[-1] == "TRUE":
                people.append({
                    "row_idx": idx,
                    "name": row[0],
                    "context": row[1] if len(row) > 1 else "",
                    "notes": row[2] if len(row) > 2 else "",
                    "follow_ups": row[3] if len(row) > 3 else "",
                    "last_touched": row[4] if len(row) > 4 else ""
                })
        
        return people
    except Exception as e:
        print(f"Memory error (get all people): {e}")
        return []


def log_to_inbox(spreadsheet, title, captured_text, bucket, confidence, timestamp, message_id):
    """Log entry to Inbox Log."""
    try:
        inbox = spreadsheet.worksheet("Inbox Log")
        inbox.append_row([
            title,
            captured_text,
            bucket.capitalize(),
            confidence,
            timestamp,
            message_id,
            ""  # fixed_to column
        ])
    except Exception as e:
        print(f"Memory error (inbox log): {e}")


def fix_entry(message_id: int, new_bucket: str, original_text: str, classification: dict) -> tuple:
    """Move an entry from one sheet to another."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        # Find the entry in all sheets by message_id
        sheets_to_check = ["People", "Ideas", "Interviews", "Things", "LinkedIn"]
        found_sheet = None
        found_row_idx = None
        found_row_data = None
        
        for sheet_name in sheets_to_check:
            try:
                sheet = spreadsheet.worksheet(sheet_name)
                all_values = sheet.get_all_values()
                
                for idx, row in enumerate(all_values[1:], start=2):
                    if len(row) >= 2:
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
            return False, None
        
        # Mark old entry as inactive
        old_sheet = spreadsheet.worksheet(found_sheet)
        is_active_col = len(found_row_data)
        old_sheet.update_cell(found_row_idx, is_active_col, "FALSE")
        
        # Update classification bucket and save
        classification["bucket"] = new_bucket
        classification["confidence"] = 1.0
        save_entry(original_text, classification, message_id)
        
        # Update Inbox Log with fixed_to
        try:
            inbox = spreadsheet.worksheet("Inbox Log")
            all_rows = inbox.get_all_values()
            for idx, row in enumerate(all_rows[1:], start=2):
                if len(row) >= 6 and str(row[5]) == str(message_id):
                    inbox.update_cell(idx, 7, new_bucket.capitalize())
                    break
        except Exception as e:
            print(f"Error updating Inbox Log fixed_to: {e}")
        
        print(f"Moved from {found_sheet} to {new_bucket}")
        return True, found_sheet
    except Exception as e:
        print(f"Memory error (fix): {e}")
        return False, None


def get_items(table_name: str, active_only: bool = True) -> list:
    """Get items from a specific table."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        sheet = spreadsheet.worksheet(table_name)
        rows = sheet.get_all_values()[1:]
        
        items = []
        for row in rows:
            if active_only:
                if len(row) >= 2 and row[-1] == "TRUE":
                    items.append(row)
            else:
                items.append(row)
        
        return items
    except Exception as e:
        print(f"Memory error (get items): {e}")
        return []


def get_digest_data() -> dict:
    """Pull data from sheets for daily digest."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        
        data = {
            "interviews": [],
            "things": [],
            "people": []
        }
        
        # Get Interviews (active ones)
        try:
            sheet = spreadsheet.worksheet("Interviews")
            rows = sheet.get_all_values()[1:]
            for row in rows:
                if len(row) >= 6 and row[-1] == "TRUE":
                    data["interviews"].append({
                        "company": row[0],
                        "role": row[1],
                        "status": row[2],
                        "next_step": row[3],
                        "date": row[4]
                    })
        except Exception as e:
            print(f"Error reading Interviews: {e}")
        
        # Get Things (open tasks)
        try:
            sheet = spreadsheet.worksheet("Things")
            rows = sheet.get_all_values()[1:]
            for row in rows:
                if len(row) >= 5 and row[-1] == "TRUE" and row[1] == "Open":
                    data["things"].append({
                        "task": row[0],
                        "status": row[1],
                        "due": row[2],
                        "next_action": row[3]
                    })
        except Exception as e:
            print(f"Error reading Things: {e}")
        
        # Get People (with follow-ups)
        try:
            sheet = spreadsheet.worksheet("People")
            rows = sheet.get_all_values()[1:]
            for row in rows:
                if len(row) >= 6 and row[-1] == "TRUE" and row[3]:
                    data["people"].append({
                        "name": row[0],
                        "context": row[1],
                        "follow_ups": row[3]
                    })
        except Exception as e:
            print(f"Error reading People: {e}")
        
        return data
    except Exception as e:
        print(f"Memory error (digest data): {e}")
        return None
