---
name: whatsapp-triage
description: WhatsApp and Email triage pipeline — message classification, task extraction, escalation, contact dedup, digests, and credit tracking integration.
version: 2.0.0
author: Leo Lau
---

# WhatsApp & Email Triage Pipeline

A multi-agent triage system that ingests WhatsApp messages and emails, classifies them using DeepSeek, extracts tasks/notes, escalates urgent items, manages contacts, and sends daily digests.

## Architecture

```
WhatsApp bridge (Baileys) ──→ batches/ ──→ triage_agent.py ──→ whatsapp_data.db
                                                                    │
Email pipeline (IMAP)     ──→ batches/ ──→ email_triage_agent.py ───┘
                                                                    │
                                              ┌─────────────────────┘
                                              ▼
                                    escalations → Telegram push
                                    tasks/notes → stored in DB
                                    digests    → scheduled send
                                    contacts   → merged via contact_manager
```

## Key Files

| Purpose | Path |
|---------|------|
| WhatsApp bridge | `/opt/data/scripts/whatsapp-bridge/` |
| WhatsApp triage agent | `/opt/data/whatsapp-messages/triage_agent.py` |
| Email triage agent | `/opt/data/email-messages/email_triage_agent.py` |
| Contact manager | `/opt/data/whatsapp-messages/contact_manager.py` |
| SQLite database | `/opt/data/whatsapp-messages/whatsapp_data.db` |
| Bridge config (credentials) | `/opt/data/whatsapp-messages/config.json` |
| Escalation config page | `https://leolau.github.io/pu-toolbox/whatsapp-escalation.html` |
| Escalation config data | `/opt/data/whatsapp-config.json` (pushed to GH) |
| Credit tracker helper | `/opt/data/track_credit_helper.py` |
| Credit tracking DB | `/opt/data/credits.db` |
| Start script (Docker) | `/opt/data/whatsapp-messages/start_all.sh` |
| Save API server | `/opt/data/serve_config_api.py` |

## Database Schema (`whatsapp_data.db`) — 15 tables

| Table | Rows (approx) | Purpose |
|-------|---------------|---------|
| `phones` | 2 | Paired WhatsApp numbers with enable/disable |
| `messages` | 30+ | Raw incoming WhatsApp messages |
| `contacts_old_backup` | 7 | Legacy contacts snapshot |
| `wa_tasks` | 3+ | Tasks extracted by WhatsApp triage |
| `wa_notes` | 0+ | Notes extracted by WhatsApp triage |
| `wa_digests` | 12+ | WhatsApp daily digest records |
| `escalations` | 7+ | Urgent items pushed to Telegram (both channels) |
| `unified_contacts` | 142+ | Deduplicated contact records across WhatsApp + email |
| `contact_handles` | 148+ | Handle-to-contact mapping (phone numbers, email addresses) |
| `contact_merge_suggestions` | 13+ | Pending merge suggestions from fuzzy dedup |
| `email_accounts` | 3 | Configured email IMAP accounts |
| `email_messages` | 228+ | Raw incoming emails |
| `email_tasks` | 10+ | Tasks extracted by email triage |
| `email_notes` | 70+ | Notes extracted by email triage |
| `email_digests` | 9+ | Email daily digest records |

## Triage Agent Behavior

### WhatsApp (`triage_agent.py`)
- Watches `/opt/data/whatsapp-messages/batches/` for new JSON batch files
- Checks if sender is a family contact → escalate immediately (no LLM for media-only)
- Skips LLM for media-only messages from non-family contacts
- For text messages: calls DeepSeek with skills from `skills/whatsapp-triage/`
- Extracts: classification, tasks, notes, escalation flag
- Writes results → `wa_tasks`, `wa_notes`, `escalations` tables
- Moves processed batches to `batches/processed/`

### Email (`email_triage_agent.py`)
- Watches `/opt/data/email-messages/batches/` for new JSON batch files
- Auto-classifies newsletters (noreply@, unsubscribe link) without LLM
- For real emails: calls DeepSeek with skills from `skills/email-triage/` + `skills/whatsapp-triage/`
- Processes contacts via `contact_manager.py` before triage
- Extracts: classification, tasks, notes, escalation flag
- Writes results → `email_tasks`, `email_notes`, `escalations` tables

### Both agents
- Run in infinite loops with 3–5s sleep between scans
- Rate-limit between batches (1s sleep)
- Print status to stdout (visible in Docker logs)

## Contact Merge System

The contact manager (`contact_manager.py`) deduplicates contacts across WhatsApp and email sources. When it finds potential matches, it creates a row in `contact_merge_suggestions` with:

- `correlation_reason` — why they match (e.g. "Fuzzy name match 84%", "Same domain + similar name")
- `confidence` — "medium" or "high"
- `status` — always "pending" until acted on

**To review all pending merges:**
```python
import sqlite3
conn = sqlite3.connect('/opt/data/whatsapp-messages/whatsapp_data.db')
conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT * FROM contact_merge_suggestions WHERE status="pending"').fetchall():
    d = dict(r)
    print(f'{d[\"id\"][:8]} | {d[\"new_display_name\"]:30s} | {d[\"correlation_reason\"]}')
```

## Escalation Config Page

At `https://leolau.github.io/pu-toolbox/whatsapp-escalation.html`:
- 2 phone numbers with clickable enable/disable toggles
- 6 family contacts (Heidi, Mokan, Mowan, Anna, Andrew, Jay) with editable name/relation/phone
- 3 escalation rules
- Save Changes (POSTs to localhost:8766 API server)
- Restart Bridge button (runs docker exec start_all.sh)

**Editable only via local API server.** The GH Pages version is read-only.

## Credit Tracking Integration

Both triage agents wrap every DeepSeek inference with credit tracking. The helper at `/opt/data/track_credit_helper.py` provides:

```python
from track_credit_helper import track_inference

# Inside call_deepseek():
def _do_api_call():
    resp = urlopen(req, timeout=30)
    data = json.loads(resp.read().decode())
    return data['choices'][0]['message']['content']
return track_inference("WhatsApp processing", _do_api_call)
```

**Task names in credits.db:**
- `"WhatsApp processing"` — WhatsApp triage LLM calls
- `"Email processing"` — email triage LLM calls

Costs appear in the dashboard grouped by task name.

## Digest System

Both agents produce hourly digests summarizing:
- Number of messages processed
- Tasks extracted
- Items escalated
- Notable patterns

Digests are sent to Telegram and recorded in `wa_digests`/`email_digests` tables.

## Restart Procedure

After editing `config.json` (family contacts, phone status, etc.):
```bash
docker exec -u hermes hermes-agent bash /opt/data/whatsapp-messages/start_all.sh
```
Or use the 🔄 Restart Bridge button on the escalation config page.
