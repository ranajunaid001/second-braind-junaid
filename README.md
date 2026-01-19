# Second Brain Bot

A personal second brain system that captures thoughts via Telegram, classifies them using AI, and stores them in Google Sheets.

## Overview

Drop a thought into Telegram → AI classifies it → Stored in the right Google Sheet tab → Get daily digests at 10AM.

## Features

- **Capture**: Send any thought to Telegram bot
- **Classify**: GPT-4o-mini classifies into 5 categories
- **Store**: Automatically routes to correct Google Sheet tab
- **People Intelligence**: Remembers everything about people, appends notes to existing profiles
- **Fix**: Correct misclassifications with `fix admin` or `fx ppl`
- **Digest**: Daily summary at 10AM via cron job
- **Top Items**: Request top items per category with `top admin`
- **Who Lookup**: Get all info about a person with `who john`

## Architecture

```
Telegram → Railway (Python) → Google Sheets
                ↓
           OpenAI GPT-4o-mini
                ↓
           cron-job.org (daily digest)
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Interface - Telegram bot, Flask endpoints, message routing |
| `classifier.py` | Compute - AI classification logic, prompts, rules |
| `memory.py` | Memory - All Google Sheets read/write operations |
| `config.py` | Config - Environment variables |
| `requirements.txt` | Python dependencies |
| `Procfile` | Railway process configuration |
| `runtime.txt` | Python version (3.11) |
| `README.md` | This file |

## Google Sheets Structure

### Tab: People
| Column | Description |
|--------|-------------|
| Name | Person's name (unique identifier) |
| Context | Who they are, how you know them |
| Notes | Running log of everything about them (auto-appends) |
| Follow-ups | Active action items |
| Last touched | Auto-updated timestamp |
| message_id | Telegram message ID |
| is_active | TRUE/FALSE |

### Tab: Ideas
| Column | Description |
|--------|-------------|
| Idea | Short title |
| One-liner | One sentence description |
| Notes | Extra details |
| message_id | Telegram message ID |
| is_active | TRUE/FALSE |

### Tab: Interviews
| Column | Description |
|--------|-------------|
| Company | Company name |
| Role | Job role |
| Status | Lead, Applied, Scheduled, Completed |
| Next step | What to do next |
| Date | Interview date if known |
| message_id | Telegram message ID |
| is_active | TRUE/FALSE |

### Tab: Admin
| Column | Description |
|--------|-------------|
| Task | Short title |
| Status | Open, Done |
| Due | Due date |
| Next action | Concrete next step |
| message_id | Telegram message ID |
| is_active | TRUE/FALSE |

### Tab: LinkedIn
| Column | Description |
|--------|-------------|
| Idea | Post topic or hook |
| Notes | Full story or details |
| Status | Draft, Posted |
| message_id | Telegram message ID |
| is_active | TRUE/FALSE |

### Tab: Inbox Log
| Column | Description |
|--------|-------------|
| Title | Short title |
| Captured text | Original message |
| Classified as | Category |
| Confidence | 0-1 score |
| Timestamp | When captured |
| message_id | Telegram message ID |
| fixed_to | If corrected, the new category |

## Environment Variables (Railway)

| Variable | Description |
|----------|-------------|
| `TELEGRAM_TOKEN` | Bot token from @BotFather |
| `OPENAI_API_KEY` | OpenAI API key |
| `GOOGLE_SHEETS_CREDS` | Service account JSON (single line) |
| `SHEET_ID` | Google Sheet ID from URL |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID for digests |

## Telegram Commands

### Capture (just send any message)
```
John works at Google and likes coffee
Pay electricity bill by Friday
draft - story about my first job rejection
Stripe is hiring for PM role
Build an app for habit tracking
```

### Who Lookup
```
who john
who john?
who sarah
```
Returns all info about a person in clean format.

### Fix Misclassification
```
fix admin
fix people
fx ppl
fx a
fx i
```

### Get Top Items
```
top admin
top people
top interviews
top ideas
top linkedin
top all
```

## People Feature

The People tab is special — it builds profiles over time:

1. First message: "John works at Google" → Creates new John entry
2. Second message: "John has 2 kids" → Appends to John's Notes
3. Third message: "John's birthday is March 15" → Appends again
4. `who john` → Returns everything about John

Notes display as bullet points:
```
👤 John
works at Google

• John works at Google and likes coffee
• John has 2 kids and loves hiking
• John's birthday is March 15

Last updated: Jan 19
```

## Classification Rules

Edit `classifier.py` to change rules:

### Force Rules (override LLM)
- Message contains "draft" → Always LinkedIn

### LLM Rules
- Person's name + info → People
- Job opportunity, company hiring → Interviews
- Pay, buy, schedule, bill → Admin
- Product idea, build, app → Ideas

### Confidence Threshold
- Above 60%: Auto-classify and save
- 60% or below: Ask user to confirm

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/digest` | GET/POST | Trigger daily digest |
| `/health` | GET | Health check |

## Cron Job Setup (cron-job.org)

1. Create account at cron-job.org
2. Add new cron job:
   - **URL**: `https://your-app.up.railway.app/digest`
   - **Schedule**: `0 10 * * *` (10AM daily)
   - **Timezone**: America/New_York (EST)

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export TELEGRAM_TOKEN=your_token
export OPENAI_API_KEY=your_key
export GOOGLE_SHEETS_CREDS='{"type":"service_account",...}'
export SHEET_ID=your_sheet_id
export TELEGRAM_CHAT_ID=your_chat_id

# Run
python main.py
```

## Troubleshooting

### "Conflict: terminated by other getUpdates request"
Multiple bot instances running. Wait a minute or reset webhook:
```
https://api.telegram.org/bot<TOKEN>/deleteWebhook?drop_pending_updates=true
```

### "Sheets error"
- Check sheet tab names match exactly (case-sensitive)
- Verify service account has edit access

### "No one found matching"
- Check People tab has data with is_active = TRUE
- Try without punctuation: `who john` not `who john?`

### Digest not sending
- Check `TELEGRAM_CHAT_ID` is correct
- Test manually: visit `/digest` endpoint

## Future Improvements

- [ ] Weekly review with misclassification report
- [ ] Multiple Johns handling (ask which one)
- [ ] Voice message support
- [ ] Image/screenshot capture
- [ ] Calendar integration
