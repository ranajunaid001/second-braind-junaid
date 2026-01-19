# Second Brain Bot

A personal second brain system that captures thoughts via Telegram, classifies them using AI, and stores them in Google Sheets.

## Overview

Drop a thought into Telegram → AI classifies it → Stored in the right Google Sheet tab → Get daily digests at 10AM.

## Features

- **Capture**: Send any thought to Telegram bot
- **Classify**: GPT-4o-mini classifies into 5 categories
- **Store**: Automatically routes to correct Google Sheet tab
- **Fix**: Correct misclassifications with `fix admin` or `fx ppl`
- **Digest**: Daily summary at 10AM via cron job
- **Top Items**: Request top items per category with `top admin`

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
| `main.py` | Main application - Telegram bot, Flask endpoints, all logic |
| `requirements.txt` | Python dependencies |
| `Procfile` | Railway process configuration |
| `runtime.txt` | Python version (3.11) |
| `README.md` | This file |

## Google Sheets Structure

### Tab: People
| Column | Description |
|--------|-------------|
| Name | Person's name |
| Context | Who they are, how you know them |
| Follow-ups | Next thing to remember/ask |
| Last touched | Timestamp |
| message_id | Telegram message ID |
| is_active | TRUE/FALSE (for soft delete) |

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
| Classified as | Category (People, Ideas, etc.) |
| Confidence | 0-1 confidence score |
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

### Capture
Just send any message. Examples:
- "Met John at conference, works at Google"
- "Pay electricity bill by Friday"
- "draft - story about my first job rejection"

### Fix Misclassification
If the bot classifies wrong, reply with:
- `fix admin` or `fx a`
- `fix people` or `fx ppl`
- `fix ideas` or `fx i`
- `fix interviews` or `fx int`
- `fix linkedin` or `fx li`

### Get Top Items
Request top items from any category:
- `top admin` or `top a`
- `top people` or `top ppl`
- `top ideas` or `top i`
- `top interviews` or `top int`
- `top linkedin` or `top li`
- `top all` — full digest

## Classification Rules

### Priority Keywords
- **LinkedIn**: Message contains "draft" → always LinkedIn
- **Interviews**: Job opportunities, companies hiring, applications
- **Admin**: Bills, appointments, errands, tasks with "pay", "buy", "schedule"
- **Ideas**: Product ideas, things to build, concepts
- **People**: Contacts, relationships, follow-ups with specific people

### Confidence Threshold
- ≥ 61%: Auto-classify and save
- ≤ 60%: Ask user to confirm category

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/digest` | GET/POST | Trigger daily digest (called by cron) |
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

## Deployment (Railway)

1. Push code to GitHub
2. Connect GitHub repo to Railway
3. Add environment variables in Railway dashboard
4. Deploy automatically on push

## Future Improvements

- [ ] Weekly review with misclassification report
- [ ] Idempotency (prevent duplicate processing)
- [ ] Offset persistence (handle restarts)
- [ ] Strict LLM validation
- [ ] Retry with backoff on failures
- [ ] Separate rules.json for classification rules
- [ ] AI evals dashboard

## Troubleshooting

### "Conflict: terminated by other getUpdates request"
Multiple bot instances running. Reset webhook:
```
https://api.telegram.org/bot<TOKEN>/deleteWebhook?drop_pending_updates=true
```

### "Sheets error"
- Check sheet tab names match exactly (case-sensitive)
- Verify service account has edit access to the sheet

### Digest not sending
- Check `TELEGRAM_CHAT_ID` is set correctly
- Test manually: visit `/digest` endpoint in browser
