# Email + Unified Contacts: Hermes Agent Implementation Plan

## Status: COMPLETE (All 9 phases done, 76/76 tests passed)
## Last Updated: 2026-07-01T18:00Z
## Instance: <your-instance-id> (configure in .env)
## Prerequisite: WhatsApp pipeline (all 8 phases complete)

---

## Architecture Overview

```
Gmail Account #1 (IMAP) → Email Poller A  ──┐
Gmail Account #2 (IMAP) → Email Poller B  ──┤
Gmail Account #N (IMAP) → Email Poller N  ──┘
                                             │
                                    Unified Email Batcher (30s window)
                                             │
                                             ▼
                                    Shared Triage Subagent ← (same deepseek-chat, same + email skills)
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                     Contact Auto-Mgmt   Task/Note     Escalation
                     (create, correlate,  Extraction    (shared table)
                      merge/ask user)
                              │              │              │
                              ▼              ▼              ▼
                         Single SQLite DB (whatsapp_data.db)
                              │
                     ┌────────┼────────┐
                     ▼        ▼        ▼
              MCP Server   Escalation  Digest
              (port 8651)  Push(TG)    (merged WA+Email)
```

---

## Key Design Decisions

1. **Gmail via IMAP** — App Passwords for auth, polling every 60s
2. **Single SQLite DB** (`whatsapp_data.db`) — email tables prefixed `email_`, shared escalations
3. **Separate config file** — `/opt/data/email-messages/config.json`
4. **Unified Contacts** — `contacts` + `contact_handles` tables replace old per-channel contact tables
5. **Contact auto-management** — new contacts created automatically, correlation checked, auto-merge for high confidence, Telegram confirmation for medium confidence
6. **Shared triage** — same deepseek-chat model, WhatsApp + email skills loaded together
7. **Shared escalations** — single `escalations` table with `channel` + `sender_email` columns
8. **Separate everything else** — `email_tasks`, `email_notes`, `email_digests` are independent from `wa_tasks`, `wa_notes`, `wa_digests`

---

## Configuration Files

### `/opt/data/email-messages/config.json`

```json
{
  "accounts": [
    {
      "id": "email1",
      "address": "user@gmail.com",
      "label": "Personal Gmail",
      "imap": {
        "host": "imap.gmail.com",
        "port": 993,
        "tls": true
      },
      "smtp": {
        "host": "smtp.gmail.com",
        "port": 587,
        "tls": true
      },
      "credentials_env": "EMAIL1_PASSWORD",
      "folders": ["INBOX"],
      "poll_interval_seconds": 60,
      "enabled": true
    },
    {
      "id": "email2",
      "address": "user@otherdomain.com",
      "label": "Work Gmail",
      "imap": {
        "host": "imap.gmail.com",
        "port": 993,
        "tls": true
      },
      "smtp": {
        "host": "smtp.gmail.com",
        "port": 587,
        "tls": true
      },
      "credentials_env": "EMAIL2_PASSWORD",
      "folders": ["INBOX"],
      "poll_interval_seconds": 60,
      "enabled": true
    }
  ],
  "batching": {
    "window_seconds": 30
  },
  "triage": {
    "model": "deepseek-chat",
    "provider": "deepseek",
    "skills_dir": "/opt/data/skills/whatsapp-triage/",
    "email_skills_dir": "/opt/data/skills/email-triage/",
    "use_hermes_memory": true
  },
  "digest": {
    "merge_with_whatsapp": true,
    "frequency_minutes": 60,
    "channel": "telegram"
  },
  "escalation": {
    "push_to_telegram": true,
    "criteria": {
      "vip_senders": [],
      "domain_rules": [],
      "rules": [
        "Any email from vip_senders -> escalate immediately",
        "Client emails needing immediate response -> escalate",
        "Sales/RFP/proposal deadlines within 24h -> escalate",
        "Invoices and payment requests -> escalate",
        "Auto-generated newsletters/marketing -> ignore"
      ]
    }
  }
}
```

---

## SQLite Schema (Single DB: `whatsapp_data.db`)

### Existing WhatsApp Tables (to be renamed/migrated)

```
BEFORE (current):              AFTER (Phase 1 migration):
──────────────────             ──────────────────────────
phones                    →    phones (unchanged)
messages                  →    messages (unchanged)
contacts                  →    DROPPED (migrated to contacts + contact_handles)
tasks                     →    wa_tasks (renamed)
notes                     →    wa_notes (renamed)
escalations               →    escalations (add channel, sender_email, sender_name, contact_id)
digests                   →    wa_digests (renamed)
```

### New Shared Tables

#### `contacts` (replaces old `contacts` table)
```sql
CREATE TABLE contacts (
  id TEXT PRIMARY KEY,            -- UUID
  display_name TEXT NOT NULL,     -- Best known name
  is_family INTEGER DEFAULT 0,   -- From WhatsApp config
  is_vip INTEGER DEFAULT 0,      -- From email config
  relation TEXT,                  -- "Wife", "Brother", etc.
  company TEXT,                   -- Extracted from email domain or content
  notes TEXT,                     -- Free-text
  auto_merged_count INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

#### `contact_handles` (all phones + emails for each contact)
```sql
CREATE TABLE contact_handles (
  id TEXT PRIMARY KEY,            -- UUID
  contact_id TEXT NOT NULL,       -- FK -> contacts.id
  handle_type TEXT NOT NULL,      -- "phone" or "email"
  handle_value TEXT NOT NULL,     -- "+85294066060" or "heidi@gmail.com"
  display_name TEXT,              -- Name as seen on this handle
  source TEXT,                    -- "whatsapp" or "email"
  first_seen TEXT,
  last_seen TEXT,
  message_count INTEGER DEFAULT 0,
  UNIQUE(handle_type, handle_value),
  FOREIGN KEY (contact_id) REFERENCES contacts(id)
);
CREATE INDEX idx_handles_value ON contact_handles(handle_type, handle_value);
CREATE INDEX idx_handles_contact ON contact_handles(contact_id);
```

#### `contact_merge_suggestions` (pending merge confirmations)
```sql
CREATE TABLE contact_merge_suggestions (
  id TEXT PRIMARY KEY,
  new_handle_type TEXT NOT NULL,      -- "phone" or "email"
  new_handle_value TEXT NOT NULL,
  new_display_name TEXT,
  new_contact_id TEXT,                -- The newly created contact
  candidate_contact_id TEXT NOT NULL, -- Existing contact to merge with
  correlation_reason TEXT,            -- Why we think it's a match
  confidence TEXT,                    -- "high" / "medium" / "low"
  status TEXT DEFAULT 'pending',      -- pending / approved / rejected / ignored
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  FOREIGN KEY (new_contact_id) REFERENCES contacts(id),
  FOREIGN KEY (candidate_contact_id) REFERENCES contacts(id)
);
```

#### `escalations` (updated — shared between WhatsApp + Email)
```sql
-- Existing table, add columns:
ALTER TABLE escalations ADD COLUMN channel TEXT DEFAULT 'whatsapp';
ALTER TABLE escalations ADD COLUMN sender_email TEXT;
ALTER TABLE escalations ADD COLUMN sender_name TEXT;
ALTER TABLE escalations ADD COLUMN contact_id TEXT REFERENCES contacts(id);
-- Backfill existing: UPDATE escalations SET channel = 'whatsapp';
```

### New Email-Only Tables

#### `email_accounts`
```sql
CREATE TABLE email_accounts (
  id TEXT PRIMARY KEY,            -- "email1", "email2"
  address TEXT UNIQUE NOT NULL,   -- "user@gmail.com"
  label TEXT,                     -- "Personal Gmail"
  last_poll TEXT,                 -- Last successful poll timestamp
  last_uid INTEGER DEFAULT 0,    -- Last seen IMAP UID (for incremental fetch)
  status TEXT DEFAULT 'active'
);
```

#### `email_messages`
```sql
CREATE TABLE email_messages (
  id TEXT PRIMARY KEY,            -- UUID
  account_id TEXT NOT NULL,       -- FK -> email_accounts.id
  from_addr TEXT NOT NULL,
  from_name TEXT,
  to_addrs TEXT,                  -- JSON array of recipients
  cc_addrs TEXT,                  -- JSON array of CC
  subject TEXT,
  body_text TEXT,                 -- Plain text body
  body_html TEXT,                 -- HTML body (if present)
  has_attachments INTEGER DEFAULT 0,
  attachment_info TEXT,           -- JSON array [{name, mimetype, size}]
  message_id TEXT UNIQUE,         -- RFC Message-ID for dedup
  in_reply_to TEXT,               -- Parent message ID
  thread_id TEXT,                 -- Thread grouping (from References header)
  folder TEXT DEFAULT 'INBOX',
  received_at TEXT NOT NULL,
  batch_id TEXT,
  raw_headers TEXT                -- Full headers for debugging
);
CREATE INDEX idx_email_from ON email_messages(from_addr);
CREATE INDEX idx_email_thread ON email_messages(thread_id);
CREATE INDEX idx_email_received ON email_messages(received_at);
```

#### `email_tasks`
```sql
CREATE TABLE email_tasks (
  id TEXT PRIMARY KEY,
  account_id TEXT,
  source_email_id TEXT,           -- FK -> email_messages.id
  description TEXT,
  due_date TEXT,
  status TEXT DEFAULT 'pending',
  priority TEXT,
  created_at TEXT
);
```

#### `email_notes`
```sql
CREATE TABLE email_notes (
  id TEXT PRIMARY KEY,
  account_id TEXT,
  source_email_id TEXT,
  content TEXT,
  created_at TEXT
);
```

#### `email_digests`
```sql
CREATE TABLE email_digests (
  id TEXT PRIMARY KEY,
  scope TEXT,                     -- "all" / "email1" / "email2"
  content TEXT,
  email_count INTEGER,
  task_count INTEGER,
  sent_at TEXT,
  channel TEXT DEFAULT 'telegram'
);
```

---

## Contact Auto-Management Logic

### Flow Diagram

```
New message arrives (WhatsApp or Email)
  │
  ├── STEP 1: Exact handle lookup
  │   SELECT * FROM contact_handles
  │   WHERE handle_type = ? AND handle_value = ?
  │     │
  │     ├── FOUND → Update last_seen, message_count → DONE
  │     └── NOT FOUND → STEP 2
  │
  ├── STEP 2: Create new contact + handle
  │   INSERT INTO contacts (display_name = sender_name)
  │   INSERT INTO contact_handles (contact_id, handle_type, handle_value)
  │   Log: "[contacts] New contact: {name} ({handle})"
  │     │
  │     └── STEP 3: Correlation check
  │
  └── STEP 3: Can this new contact be merged with an existing one?
      │
      ├── CHECK 1: Exact name match
      │   SELECT * FROM contacts WHERE display_name = ? AND id != new_id
      │     ├── 1 match → HIGH confidence
      │     └── 2+ matches → MEDIUM confidence
      │
      ├── CHECK 2: Cross-reference in message content
      │   Scan message text for phone numbers or email addresses
      │   that match existing contact_handles
      │     └── Match found → HIGH confidence
      │
      ├── CHECK 3: Same email domain + similar name
      │   (email only) Compare domain + fuzzy name match
      │     └── Match found → MEDIUM confidence
      │
      ├── CHECK 4: Config-based match
      │   Family contacts have known phones
      │   If email arrives from same name → MEDIUM confidence
      │
      └── DECISION:
          ├── HIGH confidence, single candidate →
          │   AUTO-MERGE: Move handles to existing contact, delete new contact
          │   Log: "[contacts] Auto-merged: {name} ({new_handle}) → {existing_contact}"
          │
          ├── MEDIUM confidence →
          │   CREATE SUGGESTION: Insert into contact_merge_suggestions
          │   SEND TELEGRAM:
          │     "🔗 Contact Merge Suggestion
          │      New: {name} ({handle})
          │      Possible match: {existing_name}
          │        handles: {list of existing handles}
          │      Reason: {correlation_reason}
          │      Reply: yes / no / ignore"
          │
          └── LOW / no match →
              Keep as separate contact. No notification.
```

### Telegram Confirmation Message Format

```
🔗 Contact Merge Suggestion [ID: abc123]

New handle detected:
  📧 heidi@newcompany.com ("Heidi Lui")
  First message: "Hi Leo, this is my new work email..."

Possible match with existing contact:
  👤 Heidi Lui (Wife, is_family)
     ├── 📱 +85294066060 (WhatsApp, 142 messages, last: 2h ago)
     └── 📧 heidi@gmail.com (Email, 23 messages, last: 1d ago)

Reason: Exact display name match "Heidi Lui"

Reply with one of:
  "merge abc123" → merge into existing contact
  "no abc123" → keep as separate contact
  "ignore abc123" → keep separate, don't suggest again for this pair
```

### Merge Execution

When a merge is approved (auto or manual):
1. Move all `contact_handles` from new contact to existing contact
2. Update `contacts.auto_merged_count += 1`
3. Update `contacts.updated_at`
4. Delete the now-empty new contact record
5. Update `contact_merge_suggestions.status = 'approved'`

When rejected:
1. Update `contact_merge_suggestions.status = 'rejected'`
2. Future messages from same handle won't trigger re-suggestion for same candidate

When ignored:
1. Update `contact_merge_suggestions.status = 'ignored'`
2. Same pair will never be suggested again

---

## Email-Specific Triage Skills

### `/opt/data/skills/email-triage/`

```
/opt/data/skills/email-triage/
├── SKILL.md                    ← Base email triage instructions
├── classify-emails.md          ← Email taxonomy (invoice, newsletter, urgent, meeting, etc.)
├── extract-deadlines.md        ← Deadline/due-date extraction from email body
├── thread-context.md           ← Understanding email threads/replies
├── spam-newsletter-filter.md   ← Auto-ignore marketing/newsletters/notifications
├── attachment-handling.md      ← Flag emails with important attachments
├── signature-parsing.md        ← Extract phone/email from email signatures for contact correlation
└── custom/                     ← User-added rules (hot-loaded)
```

---

## MCP Tools

### Email Tools (port 8651)

| Tool | Params | Description |
|------|--------|-------------|
| `email_search` | query, from?, account?, date_from?, date_to?, has_attachments?, limit | Full-text search |
| `email_get_recent` | account?, from?, limit | Recent emails |
| `email_get_thread` | thread_id, account? | Full email thread |
| `email_list_tasks` | status?, priority?, date_from? | Extracted email tasks |
| `email_list_notes` | date_from?, keyword? | Email notes |
| `email_get_escalations` | status?, channel? | Escalations (filter by channel) |
| `email_resolve_escalation` | escalation_id | Mark resolved |
| `email_get_stats` | account?, period? | Email/task counts |
| `email_list_accounts` | — | Connected accounts + status |

### Contact Tools (also on port 8651)

| Tool | Params | Description |
|------|--------|-------------|
| `contact_search` | query (searches name, phone, email) | Find contacts by any handle |
| `contact_get` | contact_id | Full contact with all handles |
| `contact_get_history` | contact_id, channel?, limit | All messages across all handles |
| `contact_merge` | contact_id_1, contact_id_2 | Manually merge two contacts |
| `contact_split` | contact_id, handle_id | Detach a handle into new contact |
| `contact_list` | sort_by?, is_family?, is_vip? | List contacts |
| `contact_pending_merges` | — | Show pending merge suggestions |
| `contact_resolve_merge` | suggestion_id, action (approve/reject/ignore) | Resolve a merge suggestion |

---

## Implementation Phases

### Phase 1: Database Migration + Unified Contacts
**Goal:** Migrate existing DB schema, create contacts/contact_handles, pre-seed family contacts

**Changes:**
- Rename `tasks` → `wa_tasks`
- Rename `notes` → `wa_notes`
- Rename `digests` → `wa_digests`
- Drop old `contacts` table (after migrating data)
- Create `contacts` table (unified)
- Create `contact_handles` table
- Create `contact_merge_suggestions` table
- Add columns to `escalations`: `channel`, `sender_email`, `sender_name`, `contact_id`
- Backfill escalations: `channel = 'whatsapp'`
- Migrate old contacts → new contacts + contact_handles
- Pre-seed family contacts with phone handles
- Update WhatsApp triage/batcher/MCP to use new table names

**Tests (8):**
1. TEST 1.1: Old tables renamed correctly (wa_tasks, wa_notes, wa_digests exist)
2. TEST 1.2: New contacts table created with correct schema
3. TEST 1.3: contact_handles table created with correct schema
4. TEST 1.4: contact_merge_suggestions table created
5. TEST 1.5: Family contacts migrated (6 contacts with phone handles)
6. TEST 1.6: Escalations table has new columns (channel, sender_email, sender_name, contact_id)
7. TEST 1.7: Existing escalation rows backfilled with channel='whatsapp'
8. TEST 1.8: WhatsApp pipeline still works after migration (batcher, MCP, triage)

---

### Phase 2: Email Foundation
**Goal:** Email config, email-specific tables, email triage skills directory

**Changes:**
- Create `/opt/data/email-messages/config.json`
- Create email tables: `email_accounts`, `email_messages`, `email_tasks`, `email_notes`, `email_digests`
- Create `/opt/data/skills/email-triage/` with base skill files
- Create directories for email data

**Tests (6):**
1. TEST 2.1: config.json created and valid JSON
2. TEST 2.2: All 5 email tables created in whatsapp_data.db
3. TEST 2.3: email_accounts table has correct columns
4. TEST 2.4: email_messages table has message_id UNIQUE constraint
5. TEST 2.5: Email triage skills directory has skill files
6. TEST 2.6: Email directories exist

---

### Phase 3: IMAP Poller
**Goal:** Multi-account Gmail polling via IMAP, dedup by message_id, store to DB

**Changes:**
- Create `email_poller.py` — connects to Gmail IMAP, fetches new emails
- Support multiple accounts (one poller thread per account)
- Incremental fetch using IMAP UID (only new emails since last poll)
- Parse email headers, body (text + HTML), attachments metadata
- Dedup by message_id (INSERT OR IGNORE)
- Thread grouping via References/In-Reply-To headers
- Health endpoint on port 7901

**Tests (8):**
1. TEST 3.1: Poller starts and connects to IMAP (or gracefully handles no credentials)
2. TEST 3.2: email_accounts table populated with configured accounts
3. TEST 3.3: Test email inserted into email_messages (inject test data)
4. TEST 3.4: Dedup works (same message_id not inserted twice)
5. TEST 3.5: Thread grouping populates thread_id
6. TEST 3.6: from_addr, subject, body_text parsed correctly
7. TEST 3.7: Attachment info stored as JSON
8. TEST 3.8: Poller process running (health check)

---

### Phase 4: Email Batcher
**Goal:** 30s batching window, thread-aware grouping

**Changes:**
- Create `email_batcher.py` — groups emails by sender+account with 30s window
- Output batch JSON files to `/opt/data/email-messages/batches/`
- Thread-aware: if multiple emails in same thread arrive within window, batch together

**Tests (8):**
1. TEST 4.1: Batcher starts and watches email_messages
2. TEST 4.2: Single email creates a batch after 30s
3. TEST 4.3: Multiple emails from same sender batched together
4. TEST 4.4: Emails from different senders create separate batches
5. TEST 4.5: Batch JSON file has correct structure
6. TEST 4.6: Batch includes account_id and channel="email"
7. TEST 4.7: Thread emails grouped in same batch
8. TEST 4.8: Batcher process running

---

### Phase 5: Email MCP Server + Contact Tools
**Goal:** MCP server on port 8651 with email_* and contact_* tools

**Changes:**
- Create `email_mcp_server.py` — HTTP JSON-RPC server
- 10 email tools + 8 contact tools
- Register with Hermes config

**Tests (10):**
1. TEST 5.1: Health endpoint returns running status
2. TEST 5.2: Tools list returns all tools (18 total)
3. TEST 5.3: email_search returns results
4. TEST 5.4: email_get_recent returns recent emails
5. TEST 5.5: email_list_accounts returns configured accounts
6. TEST 5.6: contact_search finds contacts by name
7. TEST 5.7: contact_search finds contacts by phone handle
8. TEST 5.8: contact_search finds contacts by email handle
9. TEST 5.9: contact_list returns all contacts with handles
10. TEST 5.10: JSON-RPC format works

---

### Phase 6: Contact Auto-Management
**Goal:** Automatic contact creation, correlation, auto-merge, Telegram confirmations

**Changes:**
- Create `contact_manager.py` — contact lifecycle logic
- Integrate into triage agent: on every new message, run contact management
- Exact handle lookup → create if new → correlation check
- Auto-merge for high confidence, Telegram ask for medium
- Handle user responses to merge suggestions via Telegram
- MCP tools: contact_merge, contact_split, contact_pending_merges, contact_resolve_merge

**Tests (10):**
1. TEST 6.1: New WhatsApp message from unknown number creates contact + handle
2. TEST 6.2: New email from unknown address creates contact + handle
3. TEST 6.3: Known handle reuses existing contact (no duplicate)
4. TEST 6.4: Exact name match triggers high-confidence auto-merge
5. TEST 6.5: Auto-merge moves handles to existing contact correctly
6. TEST 6.6: Medium confidence creates merge suggestion in DB
7. TEST 6.7: Telegram confirmation message sent for medium confidence
8. TEST 6.8: contact_pending_merges MCP tool returns pending suggestions
9. TEST 6.9: contact_merge MCP tool merges two contacts
10. TEST 6.10: contact_split MCP tool detaches a handle

---

### Phase 7: Extend Triage + Escalation for Email
**Goal:** Triage agent handles email batches, escalation pusher handles email escalations

**Changes:**
- Extend triage_agent.py to watch `/opt/data/email-messages/batches/`
- Load email-specific skills from `/opt/data/skills/email-triage/`
- Write to `email_tasks`, `email_notes` tables
- Write to shared `escalations` table with `channel='email'`
- Extend escalation_pusher.py to handle email escalations (different format)
- Email escalation format shows subject, sender, account

**Tests (10):**
1. TEST 7.1: Triage processes email batch file
2. TEST 7.2: Email classified correctly (urgent vs newsletter vs informational)
3. TEST 7.3: Task extracted from email and written to email_tasks
4. TEST 7.4: Note extracted and written to email_notes
5. TEST 7.5: VIP sender email creates escalation with channel='email'
6. TEST 7.6: Urgent business email creates escalation
7. TEST 7.7: Newsletter email does NOT create escalation
8. TEST 7.8: Escalation pushed to Telegram with email format
9. TEST 7.9: Escalation shows sender email + subject
10. TEST 7.10: Escalation linked to contact_id

---

### Phase 8: Extend Digest (Merged WhatsApp + Email)
**Goal:** Hourly digest combines both channels

**Changes:**
- Extend digest_cron.py to query both WhatsApp and Email data
- Combined summary: "X WhatsApp messages, Y emails, Z tasks, W escalations"
- Write to `wa_digests` (WhatsApp portion) and `email_digests` (Email portion)
- Single merged Telegram message

**Tests (6):**
1. TEST 8.1: Digest includes both WhatsApp and Email message counts
2. TEST 8.2: Digest includes tasks from both channels
3. TEST 8.3: Digest includes escalations from both channels
4. TEST 8.4: Digest sent to Telegram
5. TEST 8.5: wa_digests record created
6. TEST 8.6: email_digests record created

---

### Phase 9: Integration Testing
**Goal:** End-to-end verification of the complete pipeline

**Tests (10):**
1. TEST 9.1: All services running (bridges, batcher, pollers, MCP servers, triage, escalation, digest)
2. TEST 9.2: WhatsApp message → contact created → triage → task extracted
3. TEST 9.3: Email → contact created → triage → task extracted
4. TEST 9.4: Same-name WhatsApp + Email → merge suggestion or auto-merge
5. TEST 9.5: Family WhatsApp → escalation with channel='whatsapp' → Telegram
6. TEST 9.6: Urgent email → escalation with channel='email' → Telegram
7. TEST 9.7: Contact search via MCP returns all handles
8. TEST 9.8: contact_get_history returns messages from both channels
9. TEST 9.9: Merged digest sent to Telegram
10. TEST 9.10: All MCP tools callable (WhatsApp + Email + Contact)

---

## Progress Log

| Phase | Status | Started | Completed | Tests | Notes |
|-------|--------|---------|-----------|-------|-------|
| 1 | completed | 2026-07-02T00:20Z | 2026-07-02T00:30Z | 8/8 | DB migration + unified contacts |
| 2 | completed | 2026-07-02T00:30Z | 2026-07-02T00:40Z | 6/6 | Email foundation |
|| 3 | completed | 2026-07-01T15:00Z | 2026-07-01T15:40Z | 8/8 | IMAP Poller (3 Gmail accounts, 191 emails) |
|| 4 | completed | 2026-07-01T15:40Z | 2026-07-01T16:10Z | 8/8 | Email Batcher (128 batches) |
|| 5 | completed | 2026-07-01T16:10Z | 2026-07-01T17:00Z | 10/10 | Email MCP + Contact tools (17 tools, port 8651) |
|| 6 | completed | 2026-07-01T17:00Z | 2026-07-01T17:20Z | 10/10 | Contact auto-management (131 contacts, 5 auto-merged) |
|| 7 | completed | 2026-07-01T17:20Z | 2026-07-01T17:40Z | 10/10 | Email Triage + Escalation |
|| 8 | completed | 2026-07-01T17:40Z | 2026-07-01T17:50Z | 6/6 | Merged Digest (WA + Email) |
|| 9 | completed | 2026-07-01T17:50Z | 2026-07-01T18:00Z | 10/10 | Integration Testing |

**Total: 76 tests across 9 phases**

---

## Error Log

| Time | Phase | Error | Resolution |
|------|-------|-------|------------|
|| 2026-07-01 | 2 | SQL quoting in docker exec | Deployed as separate .py via base64 |

---

## File Locations

| File | Path | Purpose |
|------|------|---------|
| Email Config | `/opt/data/email-messages/config.json` | Email account configuration |
| Email Batches | `/opt/data/email-messages/batches/` | Batch JSON files |
| Email Poller | `/opt/data/email-messages/email_poller.py` | IMAP polling daemon |
| Email Batcher | `/opt/data/email-messages/email_batcher.py` | Email batching daemon |
| Email MCP | `/opt/data/email-messages/email_mcp_server.py` | MCP server (port 8651) |
| Contact Manager | `/opt/data/whatsapp-messages/contact_manager.py` | Contact auto-management |
| Email Skills | `/opt/data/skills/email-triage/` | Email-specific triage skills |
| Unified DB | `/opt/data/whatsapp-messages/whatsapp_data.db` | Single SQLite database |
| Implementation Doc | `/opt/data/email-messages/IMPLEMENTATION.md` | This file (deployed copy) |

---

## Recovery Instructions (for next agent)

1. **Read this file first** — check Progress Log for current state
2. **Check WhatsApp pipeline** — must be running (prerequisite)
3. **Check DB schema**: `docker exec hermes-agent python3 -c "import sqlite3; db=sqlite3.connect('/opt/data/whatsapp-messages/whatsapp_data.db'); print([r[0] for r in db.execute('SELECT name FROM sqlite_master WHERE type=\"table\"').fetchall()])"`
4. **Check running processes**: `docker exec hermes-agent ps aux | grep -E "bridge|batcher|poller|mcp|triage|escalation|digest"`
5. **Remote access**: Alibaba Cloud ECS Cloud Assistant API (RunCommand), Instance ID: `<your-instance-id>`, Region: `<your-region>`
6. **Docker container**: `hermes-agent` (runs as hermes user)
7. **Gmail credentials**: Stored as env vars `EMAIL1_PASSWORD`, `EMAIL2_PASSWORD` inside container
8. **Prerequisite check**: WhatsApp phases 1-8 must be complete (see `/opt/data/whatsapp-messages/IMPLEMENTATION.md`)

---

## Dependencies

- WhatsApp pipeline (all 8 phases) — COMPLETE
- Gmail App Passwords — COMPLETE (3 accounts)
- Python imaplib (built-in) — no additional packages needed
- Python email (built-in) — for parsing MIME messages
