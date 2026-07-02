# WhatsApp + Hermes Agent: Dual-Number Implementation Plan

## Status: COMPLETE (All 8 Phases)
## Last Updated: 2026-07-01T14:50Z
## Instance: <your-instance-id> (configure in .env)

---

## Architecture Overview

```
WhatsApp Phone #1 → Bridge A (port 3000, session-phone1)  ──┐
                                                             ├──→ Unified Batcher (5s window)
WhatsApp Phone #2 → Bridge B (port 3001, session-phone2)  ──┘          │
                                                                       ▼
                                                              Triage Subagent
                                                         (delegate_task, background=true)
                                                         (deepseek-chat, skill-based)
                                                                       │
                                                                       ▼
                                                              SQLite DB + MCP Server
                                                                       │
                                                    ┌──────────────────┼──────────────────┐
                                                    ▼                  ▼                  ▼
                                           Escalation Push      Hourly Digest      On-demand Query
                                           (→ Telegram)         (→ Telegram)       (via MCP tools)
```

---

## Key Design Decisions

1. **Two bridge processes** (port 3000 + 3001), each with independent WhatsApp sessions
2. **Single SQLite DB** with `source_phone` column on all tables
3. **Unified contacts** — same phone number on both accounts = same contact
4. **Shared triage rules** — both numbers processed identically
5. **Skills-based triage** — dynamic .md files loaded at runtime, no code changes needed
6. **Hermes memory integration** — corrections via Telegram update memory, triage reads it
7. **MCP server** as the unified query layer for the Hermes Agent
8. **Hybrid escalation** — family contacts + model-classified urgent → push to Telegram immediately
9. **5s batching window**, **hourly digest**, **deepseek-chat** for triage

---

## Configuration Files

### `/opt/data/whatsapp-messages/config.json`

```json
{
  "phones": [
    {
      "id": "phone1",
      "number": "auto-detected-after-pairing",
      "label": "Phone 1",
      "bridge_port": 3000,
      "session_dir": "/opt/data/platforms/whatsapp/session-phone1",
      "enabled": true
    },
    {
      "id": "phone2",
      "number": "auto-detected-after-pairing",
      "label": "Phone 2",
      "bridge_port": 3001,
      "session_dir": "/opt/data/platforms/whatsapp/session-phone2",
      "enabled": true
    }
  ],
  "batching": {
    "window_seconds": 5
  },
  "triage": {
    "model": "deepseek-chat",
    "provider": "deepseek",
    "skills_dir": "/opt/data/skills/whatsapp-triage/",
    "custom_skills_dir": "/opt/data/skills/whatsapp-triage/custom/",
    "use_hermes_memory": true
  },
  "digest": {
    "frequency_minutes": 60,
    "channel": "telegram"
  },
  "escalation": {
    "push_to_telegram": true,
    "criteria": {
      "family_contacts": [
        { "name": "Heidi Lui", "relation": "Wife", "phone": "+85294066060" },
        { "name": "Mokan Lau", "relation": "Elder Son", "phone": "+85252395796" },
        { "name": "Mowan Lau", "relation": "Younger Son", "phone": "+85291318528" },
        { "name": "Anna Hau", "relation": "Mother", "phone": "+14167239963" },
        { "name": "Andrew Lau", "relation": "Brother", "phone": "+85262776600" },
        { "name": "Jay Lau", "relation": "Sister-in-law", "phone": "+85262288510" }
      ],
      "rules": [
        "Any message from family_contacts -> escalate immediately",
        "Business requests needing immediate attention -> escalate",
        "Sales opportunities needing immediate attention -> escalate"
      ]
    }
  },
  "mcp_server": {
    "port": 8650,
    "transport": "sse"
  }
}
```

---

## SQLite Schema

```sql
CREATE TABLE phones (
  id TEXT PRIMARY KEY,
  number TEXT UNIQUE NOT NULL,
  label TEXT,
  paired_at TEXT,
  status TEXT DEFAULT 'active'
);

CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  source_phone TEXT NOT NULL,
  sender_phone TEXT NOT NULL,
  sender_name TEXT,
  chat_id TEXT,
  is_group INTEGER DEFAULT 0,
  text TEXT,
  media_type TEXT,
  media_path TEXT,
  media_mimetype TEXT,
  timestamp TEXT NOT NULL,
  received_at TEXT NOT NULL,
  batch_id TEXT,
  raw_json TEXT
);

CREATE TABLE contacts (
  phone TEXT PRIMARY KEY,
  name TEXT,
  is_family INTEGER DEFAULT 0,
  relation TEXT,
  first_seen TEXT,
  last_seen TEXT,
  message_count INTEGER DEFAULT 0
);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  source_phone TEXT,
  source_msg_id TEXT,
  description TEXT,
  due_date TEXT,
  status TEXT DEFAULT 'pending',
  priority TEXT,
  created_at TEXT
);

CREATE TABLE notes (
  id TEXT PRIMARY KEY,
  source_phone TEXT,
  source_msg_id TEXT,
  content TEXT,
  created_at TEXT
);

CREATE TABLE escalations (
  id TEXT PRIMARY KEY,
  source_phone TEXT,
  source_msg_id TEXT,
  sender_phone TEXT,
  reason TEXT,
  summary TEXT,
  priority TEXT,
  status TEXT DEFAULT 'pending',
  created_at TEXT,
  delivered_at TEXT,
  resolved_at TEXT
);

CREATE TABLE digests (
  id TEXT PRIMARY KEY,
  scope TEXT,
  content TEXT,
  message_count INTEGER,
  task_count INTEGER,
  sent_at TEXT,
  channel TEXT DEFAULT 'telegram'
);
```

---

## MCP Server Tools

| Tool | Params | Description |
|------|--------|-------------|
| `whatsapp_search_messages` | query, sender?, phone?, date_from?, date_to?, is_group?, limit? | Full-text search across messages |
| `whatsapp_get_recent` | phone?, sender?, limit? | Get most recent messages |
| `whatsapp_list_tasks` | status?, priority?, date_from? | List extracted tasks |
| `whatsapp_list_notes` | date_from?, keyword? | List notes |
| `whatsapp_list_contacts` | sort_by?, is_family? | Unified contacts list |
| `whatsapp_get_escalations` | status? | Pending/recent escalations |
| `whatsapp_resolve_escalation` | escalation_id | Mark escalation resolved |
| `whatsapp_get_stats` | phone?, period? | Message/task counts and summaries |
| `whatsapp_get_conversation` | contact_phone, phone?, limit? | Full thread with a contact |
| `whatsapp_list_phones` | - | Connected numbers + status |

---

## Triage Skills Structure

```
/opt/data/skills/whatsapp-triage/
├── SKILL.md                     ← base triage instructions + routing
├── classify-messages.md         ← message classification taxonomy
├── extract-tasks.md             ← task extraction patterns
├── extract-contacts.md          ← contact recognition
├── sales-opportunities.md       ← sales lead detection
├── business-urgency.md          ← business urgency criteria
└── custom/                      ← user adds new skills here (hot-loaded)
    └── (empty initially)
```

---

## Implementation Phases

---

### Phase 1: Foundation (Config + Schema + Phone Sessions)

**Tasks:**
- [ ] Write `config.json` to instance
- [ ] Create SQLite database with all tables
- [ ] Move existing phone1 session to `session-phone1` directory
- [ ] Prepare `session-phone2` directory for second pairing
- [ ] Create triage skills directory structure with base skill files

**Tests:**
- [ ] TEST 1.1: Verify config.json is valid JSON and readable
- [ ] TEST 1.2: Verify SQLite DB created with all 7 tables (query each table)
- [ ] TEST 1.3: Verify session-phone1 contains valid creds.json
- [ ] TEST 1.4: Verify session-phone2 directory exists and is empty
- [ ] TEST 1.5: Verify skills directory structure exists with all base files
- [ ] TEST 1.6: Verify all paths are owned by hermes user

**Status:** NOT STARTED

---

### Phase 2: Dual Bridge Setup

**Tasks:**
- [ ] Start Bridge A (port 3000) with existing phone1 session
- [ ] Verify Bridge A connects and shows "connected" status
- [ ] Generate QR code for Phone #2 pairing
- [ ] User scans QR → Bridge B session established
- [ ] Start Bridge B (port 3001) with phone2 session
- [ ] Update config.json with detected phone numbers
- [ ] Create startup script for dual-bridge

**Tests:**
- [ ] TEST 2.1: Bridge A health endpoint returns `{"status":"connected"}`
- [ ] TEST 2.2: Bridge B health endpoint returns `{"status":"connected"}`
- [ ] TEST 2.3: Both bridges running simultaneously (ps aux check)
- [ ] TEST 2.4: Send test message to Phone #1 → appears in bridge A queue
- [ ] TEST 2.5: Send test message to Phone #2 → appears in bridge B queue
- [ ] TEST 2.6: Messages don't cross (Phone 1 msg only in bridge A, not B)
- [ ] TEST 2.7: config.json updated with real phone numbers
- [ ] TEST 2.8: Kill and restart both bridges via start.sh → both reconnect

**Status:** NOT STARTED
**Notes:** Phone #1 is already paired from earlier session. Only Phone #2 needs QR pairing.

---

### Phase 3: Unified Batcher

**Tasks:**
- [ ] Create `batcher.mjs` (Node.js) that polls both bridge endpoints
- [ ] Implement 5-second debounce window per sender+source_phone
- [ ] Write raw messages to SQLite immediately on receipt
- [ ] Emit completed batches (after 5s silence) as JSON to batch output queue
- [ ] Tag every message with source_phone (phone1/phone2)
- [ ] Handle media downloads (save to /opt/data/whatsapp-messages/media/)
- [ ] Auto-detect and update contact records in contacts table

**Tests:**
- [ ] TEST 3.1: Single message from Phone 1 → appears in messages table within 1s
- [ ] TEST 3.2: Single message from Phone 2 → appears in messages table within 1s
- [ ] TEST 3.3: Burst of 5 messages from same sender in 3s → grouped into 1 batch
- [ ] TEST 3.4: Messages from 2 different senders → 2 separate batches
- [ ] TEST 3.5: Message with image → media downloaded and path stored in DB
- [ ] TEST 3.6: Contact table updated (name, last_seen, message_count)
- [ ] TEST 3.7: Family contact detected → is_family=1 set in contacts table
- [ ] TEST 3.8: source_phone correctly set for messages from each bridge
- [ ] TEST 3.9: Same contact messaging on both phones → single contact record, 2 messages
- [ ] TEST 3.10: Batcher survives bridge temporary disconnect → reconnects and resumes

**Status:** NOT STARTED

---

### Phase 4: MCP Server

**Tasks:**
- [ ] Create MCP server (Python, using `mcp` SDK or `fastmcp`)
- [ ] Implement all 10 tools (search, recent, tasks, notes, contacts, escalations, resolve, stats, conversation, phones)
- [ ] Full-text search uses SQLite FTS5 or LIKE queries
- [ ] Optional `phone` parameter on all relevant tools
- [ ] Start MCP server on port 8650 (SSE transport)
- [ ] Register MCP server in Hermes config.yaml

**Tests:**
- [ ] TEST 4.1: MCP server starts and responds to tool list request
- [ ] TEST 4.2: `whatsapp_list_phones` returns both configured phones
- [ ] TEST 4.3: `whatsapp_get_recent(limit=5)` returns last 5 messages
- [ ] TEST 4.4: `whatsapp_search_messages(query="hello")` finds matching messages
- [ ] TEST 4.5: `whatsapp_search_messages(phone="phone1")` filters correctly
- [ ] TEST 4.6: `whatsapp_list_contacts(is_family=true)` returns 6 family members
- [ ] TEST 4.7: `whatsapp_get_conversation(contact_phone="+85294066060")` returns thread
- [ ] TEST 4.8: `whatsapp_get_stats(period="today")` returns correct counts
- [ ] TEST 4.9: Hermes Agent can discover and call MCP tools (integration test)
- [ ] TEST 4.10: Concurrent requests don't corrupt SQLite (basic load test)

**Status:** NOT STARTED

---

### Phase 5: Triage Subagent

**Tasks:**
- [ ] Create base triage skill files (.md) with classification taxonomy
- [ ] Implement triage invocation: batcher emits batch → calls delegate_task
- [ ] Triage reads skills from skills_dir
- [ ] Triage classifies each message (task/note/urgent/informational/ignorable)
- [ ] Triage extracts entities: tasks with due dates, notes, contacts
- [ ] Triage writes structured output to SQLite (tasks, notes tables)
- [ ] Triage checks family_contacts list for immediate escalation
- [ ] Triage uses deepseek-chat model (not Reasoner)
- [ ] Triage accesses Hermes memory for learned patterns

**Tests:**
- [ ] TEST 5.1: Send "Meeting tomorrow at 3pm with John" → task extracted with due_date
- [ ] TEST 5.2: Send "Remember to buy milk" → note extracted
- [ ] TEST 5.3: Message from wife (+85294066060) → escalation created with reason="family"
- [ ] TEST 5.4: Send "Urgent: client needs proposal by EOD" → escalation with reason="urgent_business"
- [ ] TEST 5.5: Send "Hi, interested in buying 100 units" → escalation with reason="sales_opportunity"
- [ ] TEST 5.6: Send "lol ok" → classified as ignorable, no task/note/escalation
- [ ] TEST 5.7: Verify triage uses deepseek-chat (check logs for model name)
- [ ] TEST 5.8: Add custom skill file → next triage run picks it up without restart
- [ ] TEST 5.9: Multiple batches processed concurrently without data corruption
- [ ] TEST 5.10: Triage completes within 10s for a batch of 20 messages

**Status:** NOT STARTED

---

### Phase 6: Escalation Push to Telegram

**Tasks:**
- [ ] Implement push logic: escalation written → send to Telegram via gateway API
- [ ] Format escalation message nicely (contact name, reason, summary, source phone)
- [ ] Include context (last few messages in the thread for context)
- [ ] Handle main agent's response (resolve escalation when actioned)
- [ ] Rate-limit pushes (no more than 1 per contact per 5 minutes for same topic)

**Tests:**
- [ ] TEST 6.1: Family contact message → Telegram notification received within 10s
- [ ] TEST 6.2: Urgent business message → Telegram notification received within 15s
- [ ] TEST 6.3: Notification format includes: sender name, reason, message preview, source phone label
- [ ] TEST 6.4: Non-urgent message → NO Telegram notification (only in digest)
- [ ] TEST 6.5: Duplicate escalation from same contact within 5min → deduplicated (single notification)
- [ ] TEST 6.6: Escalation status changes to "delivered" after Telegram send
- [ ] TEST 6.7: Main agent can resolve escalation via MCP tool → status = "resolved"
- [ ] TEST 6.8: Telegram channel remains responsive during escalation pushes (non-blocking)

**Status:** NOT STARTED

---

### Phase 7: Hourly Digest

**Tasks:**
- [ ] Create digest generation logic (queries SQLite for undigested items since last digest)
- [ ] Format digest: message counts, new tasks, key conversations, notable items
- [ ] Send digest to Telegram via gateway API
- [ ] Set up cron/scheduled job (every 60 minutes)
- [ ] Mark items as "digested" after inclusion
- [ ] Skip empty digests (no notification if nothing new)

**Tests:**
- [ ] TEST 7.1: After 5+ messages received → hourly digest includes them
- [ ] TEST 7.2: Digest format shows: message count per phone, new tasks, contact activity
- [ ] TEST 7.3: Items from previous digest NOT repeated in next digest
- [ ] TEST 7.4: Empty period → no digest sent (silent)
- [ ] TEST 7.5: Escalated items appear in digest with "already escalated" note
- [ ] TEST 7.6: Digest sent to Telegram successfully
- [ ] TEST 7.7: Digest record written to digests table with correct counts
- [ ] TEST 7.8: Cron job fires reliably every 60 minutes (check scheduled job list)

**Status:** NOT STARTED

---

### Phase 8: Integration Testing & Feedback Loop

**Tasks:**
- [ ] End-to-end test: message → batcher → triage → escalation → Telegram
- [ ] End-to-end test: message → batcher → triage → task → digest → Telegram
- [ ] Test main agent querying MCP during Telegram conversation
- [ ] Test feedback loop: correct triage via Telegram → memory updated → next triage improved
- [ ] Test system under load (simulate 50 messages in 1 minute)
- [ ] Verify container restart recovery (start.sh brings everything back)
- [ ] Document any manual steps or known issues

**Tests:**
- [ ] TEST 8.1: Full pipeline: WhatsApp msg → SQLite → MCP queryable → Telegram digest (< 65s)
- [ ] TEST 8.2: Ask via Telegram "what messages did I get today?" → agent queries MCP → correct answer
- [ ] TEST 8.3: Ask via Telegram "any tasks from wife?" → agent finds family messages
- [ ] TEST 8.4: Tell agent "messages from Andrew about dinner should always be escalated" → memory updated
- [ ] TEST 8.5: Next Andrew dinner message → escalated (proving memory feedback works)
- [ ] TEST 8.6: 50 messages in 1 minute → all processed, no data loss, system stable
- [ ] TEST 8.7: Kill all processes → run start.sh → everything recovers within 30s
- [ ] TEST 8.8: Both bridges still connected after 1 hour uptime (no session drops)
- [ ] TEST 8.9: Telegram responsiveness unaffected by WhatsApp load (< 3s response time)
- [ ] TEST 8.10: MCP tools return correct results after heavy ingestion

**Status:** NOT STARTED

---

## Progress Log

| Date | Phase | Action | Result | Notes |
|------|-------|--------|--------|-------|
| 2026-07-01 | Pre-work | WhatsApp Phone #1 paired via QR | SUCCESS | Session at /opt/data/platforms/whatsapp/session/ |
| 2026-07-01 | Pre-work | Bridge started standalone (bot mode, all users) | SUCCESS | Port 3000, WHATSAPP_ALLOWED_USERS=* |
| 2026-07-01 | Pre-work | Basic collector running (writes to messages.json) | SUCCESS | Will be replaced by batcher in Phase 3 |
| 2026-07-01 | Pre-work | Gateway WhatsApp disabled (WHATSAPP_ENABLED=false) | SUCCESS | Prevents auto-reply |
| 2026-07-01 | Phase 1 | config.json written | SUCCESS | 2 phones, 6 family contacts, all settings |
| 2026-07-01 | Phase 1 | SQLite DB created with 7 tables | SUCCESS | All tables + family contacts pre-populated |
| 2026-07-01 | Phase 1 | Session dirs set up (phone1 has creds, phone2 empty) | SUCCESS | Ready for QR pairing |
| 2026-07-01 | Phase 1 | Triage skills dir created with 6 base files | SUCCESS | SKILL.md + 5 classification/extraction skills |
| 2026-07-01 | Phase 1 | ALL TESTS PASSED (1.1-1.6) | SUCCESS | Phase 1 complete |
| | | | | |

---

## File Locations on Instance

| File | Path | Purpose |
|------|------|---------|
| Config | `/opt/data/whatsapp-messages/config.json` | User-editable configuration |
| SQLite DB | `/opt/data/whatsapp-messages/whatsapp_data.db` | All structured data |
| Messages media | `/opt/data/whatsapp-messages/media/` | Downloaded attachments |
| Bridge A log | `/opt/data/whatsapp-messages/bridge-phone1.log` | Bridge A stdout/stderr |
| Bridge B log | `/opt/data/whatsapp-messages/bridge-phone2.log` | Bridge B stdout/stderr |
| Batcher log | `/opt/data/whatsapp-messages/batcher.log` | Batcher process log |
| MCP server log | `/opt/data/whatsapp-messages/mcp.log` | MCP server log |
| Triage log | `/opt/data/whatsapp-messages/triage.log` | Triage agent log |
| Escalation log | `/opt/data/whatsapp-messages/escalation.log` | Escalation pusher log |
| Digest log | `/opt/data/whatsapp-messages/digest.log` | Hourly digest log |
| Startup script | `/opt/data/whatsapp-messages/start_all.sh` | Starts ALL services |
| Triage skills | `/opt/data/skills/whatsapp-triage/` | Dynamic skill files |
| Session Phone 1 | `/opt/data/platforms/whatsapp/session-phone1/` | Baileys auth for phone 1 |
| Session Phone 2 | `/opt/data/platforms/whatsapp/session-phone2/` | Baileys auth for phone 2 |

---

## Recovery Instructions (for next agent)

If picking up this work mid-implementation:

1. **Check current state**: Run `docker exec hermes-agent cat /opt/data/whatsapp-messages/config.json` to see what's configured
2. **Check running processes**: `docker exec hermes-agent ps aux | grep -E "bridge|batcher|collector|mcp"`
3. **Check bridge health**: `docker exec hermes-agent curl -s http://localhost:3000/health` (and :3001 for bridge B)
4. **Check DB exists**: `docker exec hermes-agent ls -la /opt/data/whatsapp-messages/whatsapp_data.db`
5. **Check last progress**: Read the Progress Log table above
6. **Remote access method**: Alibaba Cloud ECS Cloud Assistant API (RunCommand) — no SSH available
7. **Instance ID**: `<your-instance-id>`, Region: `<your-region>`
8. **Docker container**: `hermes-agent` (runs as hermes user inside)
9. **Hermes user**: All files must be owned by `hermes:hermes` inside the container
10. **Bridge resolved dir**: `resolve_whatsapp_bridge_dir()` returns `/opt/data/scripts/whatsapp-bridge` (NOT the install dir)

---

## Known Issues / Gotchas

1. **npm install fails in gateway context** — The gateway's adapter resolves bridge dir to `/opt/data/scripts/whatsapp-bridge` (mirrored location). Hash stamp must exist there. Fixed by creating `.hermes-pkg-hash` in the mirrored `node_modules/`.
2. **Bridge self-chat mode** — Default `WHATSAPP_MODE=self-chat` rejects non-self messages. Must set `WHATSAPP_MODE=bot` and `WHATSAPP_ALLOWED_USERS=*`.
3. **File permissions** — All files under `/opt/data/` must be owned by `hermes:hermes` for bridge/batcher to work.
4. **Session path** — The adapter expects sessions at `get_hermes_dir("platforms/whatsapp/session")` which resolves to `/opt/data/platforms/whatsapp/session/`.
5. **Container restarts** — s6-overlay may restart the gateway, which could conflict with standalone bridge processes. Keep `WHATSAPP_ENABLED=false` in `.env`.
