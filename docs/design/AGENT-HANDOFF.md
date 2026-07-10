# Agent Hand-off Note — Hermes multi-channel redesign

**Purpose:** Let the next agent pick up the architecture/design work without re-deriving context. This captures the state of the design effort, the decisions made, the live-system facts, and the concrete next steps. Nothing here has been implemented on the live box — this is design/planning only.

**Last updated:** 2026-07 (by the previous session)

---

## 1. What this work is

The user (Leo, `leolau@joyaether.com`) runs a personal Hermes deployment and wants to add three integrated capabilities and unify channel handling. The effort split into two design tracks:

- **Design #1 (frozen):** the original detailed plan — skills-focus routing + per-focus self-improvement, Telegram human-in-the-loop (approval-by-default), per-focus MCP servers + self-improving MCP factory, plus an OSS→internal integration pipeline. Full document committed alongside this note: **`docs/design/architecture-design-number-one.md`**. Treat it as read-only history.
- **Design #2 (in progress — this is the active track):** a blank-sheet redesign. The user explicitly chose "blank sheet with new goals." So far only the **first concrete concern** has been designed in depth (below); the rest of #2 is not yet written up. No `architecture-design-number-two.md` exists yet — its decisions live in this note until the design is finalized.

### ★ KEY DESIGN PRINCIPLE (overrides everything — carried from #1)
Every WhatsApp number, every email address, and every calendar is just a different **incoming channel** following the same pattern. Once a message/event comes in, it is handled by **ONE shared agent infrastructure**: the same skills, the same memory, the same context — **one brain, one profile** (`HERMES_HOME=/opt/data`), no per-channel/per-account silos. This principle takes precedence over all other design details.

---

## 2. Design #2 — the active problem and the decided approach

**Problem the user posed:** the live system added multi-WhatsApp-number, multi-email, and multi-calendar support, but that implementation lives in bespoke standalone scripts (`custom/whatsapp/*`, `custom/email/*`, `custom/calendar/*`) and **never connects to the real Hermes agent loop** (the `AIAgent` core). This is the "unified storage, siloed cognition" gap. Goal: support multi-account ingestion **while routing everything through the same agent loop**.

### Code findings that constrain the design (all verified in this repo)
- **Native gateway supports exactly one account per platform type.** `GatewayConfig.platforms` is `Dict[Platform, PlatformConfig]` (`gateway/config.py:529`) — one config → one adapter → one credential. Email adapter holds a single `_address`/`_imap_host` (`plugins/platforms/email/adapter.py:437-439`); WhatsApp Cloud holds a single `_phone_number_id` (`gateway/platforms/whatsapp_cloud.py:198`).
- **Calendar is not a gateway platform at all** — no inbound-webhook concept; it is inherently poll-based.
- **Sessions key on the sender, not on "which of my accounts received it."** `build_session_key` → `{ns}:{platform}:{chat_type}:{chat_id}[:{thread_id}]` (`gateway/session.py:783,817-827`). `SessionSource` (`session.py:131-157`) has no "receiving account" field (only `profile`, used by multiplexing).
- **`multiplex_profiles` is the only native multi-tenant seam — and it is REJECTED here** because each profile gets its own memory/skills, which violates the one-brain principle.

### Decided approach (split ingestion vs. processing)
1. **Keep the multi-account ingestion that already works.** The pollers already know how to talk to N mailboxes / N numbers / N calendars. Keep them as **thin producers only** — strip their bespoke DeepSeek triage. Each emits a normalized inbound event tagged `(platform, my_account_id, sender_chat_id, payload)`.
2. **Route every event through the native session manager** so it hits the same `AIAgent` core (same skills/memory/profile). Small, additive, cache-safe extension required:
   - **Add `account_id` (receiving-inbox identity) to `SessionSource`** and fold it into the session key, so per-account conversations don't collide and **egress replies go out via the correct account**.
3. **Two shapes, sequenced:**
   - **Shape 1 (do first — CHOSEN transport: in-process inbound queue):** producers push events into an **in-process inbound queue inside the gateway** → a bounded async worker pool → each worker owns one session's cached core. No adapter rewrite; reuse the working pollers. Fixes the siloed-cognition gap immediately.
   - **Shape 2 (durable target):** teach the gateway to run **N instances of an adapter, one per account** (extend `PlatformConfig` with an `accounts:` list). Clean Hermes-native end state / upstream-shaped PR. Migrate after Shape 1 proves out.
4. **Calendar = a cron/heartbeat producer** that emits events into the same inbound queue (`account_id` = which calendar), so calendar flows through the identical agent loop as messages.

Everything stays on the **Footprint Ladder** — no new core tools; it's a gateway/config + adapter extension plus a cron producer. One `HERMES_HOME=/opt/data` profile throughout.

### Concurrency model (clarified with the user — important)
- **Latency ≠ throughput.** ~30s/message is per-message *latency*; inference is I/O-bound, so many sessions' turns overlap on asyncio. Throughput is not 1 msg/30s.
- **Hermes already runs multiple agent-cores concurrently** — one cached `AIAgent` per `session_key` (`gateway/run.py` ~2757, "Cache AIAgent instances per session to preserve prompt caching"). N accounts/conversations = N cores in one process, all under one profile → they already share skills+memory+context.
- **Shared-resource access control already exists:**
  - Memory (`MEMORY.md`/`USER.md`): read as a **frozen snapshot at session start** (zero read contention); writes guarded by a **cross-process exclusive file lock** (`fcntl.flock`) with **re-read-under-lock + atomic replace** (`tools/memory_tool.py:245-267,347-348`).
  - SessionDB (`state.db`): **SQLite WAL** → concurrent readers + serialized writers; each session writes only its own rows.
  - Skills: read-only at runtime; `skill_manage` writes are rare.
- **Two hard limits:** (a) you can run many cores over many *sessions* but **NOT** many cores over one *live conversation* (prompt cache requires a byte-stable prefix + strict role alternation + serial turns) — so parallelism is at **session granularity**; (b) shared memory is **eventually consistent** — a fact written in session A only enters session B's prompt at B's next session start (snapshot is frozen for cache safety).

### Session definition (clarified with the user)
A **session** = one bounded conversation context: identity `session_key` → persistence `session_id` (a SessionDB row, with `parent_session_id` chains from compaction) → live cached `AIAgent`. Mapping is **~1:1 `chat_id`↔session for DMs**; a group has one `chat_id` shared by many participants (optionally split per-user via `group_sessions_per_user`); a group `chat_id` with threads splits per `thread_id`; `/reset` and compaction give one key multiple `session_id`s over time. A **DM** = a 1:1 private conversation (`chat_type="dm"`, `session.py:134`).

---

## 3. THE key open decision for design #2

**Memory consistency model.** The user selected "let me think / discuss more" — this is unresolved and blocks finalizing #2. Options presented:
- **Snapshot-per-session** (cheapest, cache-safe, eventual consistency — matches Hermes today).
- **Real-time shared memory** across concurrent cores (richer; needs a DB-backed memory provider; breaks the frozen-snapshot cache optimization; costlier).
- **Hybrid** (recommended framing to revisit): keep the cheap frozen snapshot for the *prompt* (slow/curated facts), and add a **real-time queryable store** — the existing shared SQLite (`/opt/data/whatsapp-messages/whatsapp_data.db`) is already that store — for fast-changing coordination state (in-flight/handled/dedupe/per-lead status) read/written via a tool mid-turn. The insight to raise with the user: "memory" conflates slow curated facts (belong in `MEMORY.md`, snapshot is fine) vs. volatile coordination state (belongs in a queryable store, not `MEMORY.md`).

**Resume the conversation here.** Get the user's decision, then write `architecture-design-number-two.md`.

---

## 4. Live system facts (verified this session)

- **Host:** Alibaba Cloud ECS, region **cn-hongkong**, instance **`i-j6camnt3ocwlmzajthil`**, public IP **8.217.86.90**.
- **Runtime:** Docker container **`hermes-agent`**, `HERMES_HOME=/opt/data` (one unified profile).
- **How to reach the box from the agent VM:** no SSH key on file; use **`aliyun` CLI → ECS RunCommand** (Cloud Assistant). Credentials are in env (`ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`). Pattern:
  ```bash
  # base64-encode a shell script, run it in the container, poll for output
  ENC=$(base64 -w0 script.sh)
  IID=$(aliyun ecs RunCommand --region cn-hongkong --RegionId cn-hongkong \
        --InstanceId.1 i-j6camnt3ocwlmzajthil --Type RunShellScript \
        --Timeout 90 --ContentEncoding Base64 --CommandContent "$ENC" \
        | python3 -c "import sys,json;print(json.load(sys.stdin)['InvokeId'])")
  aliyun ecs DescribeInvocationResults --region cn-hongkong --RegionId cn-hongkong --InvokeId "$IID"
  # Output field is base64-encoded. Commands inside run: docker exec hermes-agent sh -lc '...'
  ```
- **Model in use:** **`deepseek-reasoner`** (provider `deepseek`) — `model.default` in `/opt/data/config.yaml` (config version 30). Every per-role/per-platform `model:` override is blank → all fall back to the default (router, specialists, background review, curator, compression all currently on `deepseek-reasoner`).
- **Only model key set:** `DEEPSEEK_API_KEY` (in `/opt/data/.env`). `providers: {}` is empty.
- **Grok is NOT active:** `x_search.model: grok-4.20-reasoning` is a shipped default placeholder; with no xAI/Grok key and no provider, X-search is inert. (The user asked about this specifically — reassure them nothing is secretly using Grok.)
- **Design-#2 optimization to remember:** since one model powers both routing and heavy reasoning, a two-stage split should point the **router** at a cheaper model (e.g. `deepseek-chat`) via a per-role `model:` override, keeping specialists on `deepseek-reasoner`. No core change.

### Live pipeline layout (from §1.6 audit in design #1)
- `custom/whatsapp/` (triage_agent.py, batcher.py, escalation_pusher.py, digest_cron.py, mcp_server.py, telegram_callback_handler.py), `custom/email/` (email_triage_agent.py, email_poller.py, email_batcher.py, email_mcp_server.py), `custom/calendar/` (calendar_auth.py, calendar_poller.py), `custom/migrations/create_calendar_tables.py`.
- Shared SQLite: `/opt/data/whatsapp-messages/whatsapp_data.db` (WhatsApp, email, and calendar all write here).
- Skills today are channel-siloed: `/opt/data/skills/{whatsapp-triage/, email-triage/}` (asymmetric — email loads both, WhatsApp loads only its own). Memory (`/opt/data/memories/`) is currently a no-op in the triage scripts.
- Native platform adapters exist and are the target for the loop-connected design: `plugins/platforms/{whatsapp,email,telegram}/adapter.py`.

---

## 5. §1.6 conformance audit (what to fix, from design #1)
Against the ★ principle: **data layer conforms** (one profile, one shared DB) but **cognition/skills/memory layer violates it**:
1. Two separate brains (standalone scripts, not the shared `AIAgent`). → Fix: route through the one agent loop (Design #2 §2).
2. Skills channel-siloed and asymmetric. → Fix: one shared skill pool + per-focus scoping.
3. Shared memory is a no-op. → Fix: native `background_review` + shared memory (subject to the consistency decision in §3).
4. Calendar absent from the unified path. → Fix: calendar cron producer into the same queue.

---

## 6. Next steps for the incoming agent
1. **Resume at the memory-consistency decision (§3)** — it's the gate to writing design #2.
2. Once decided, write **`docs/design/architecture-design-number-two.md`**: full Shape 1 → Shape 2 migration, the `account_id` change (SessionSource + session key + egress routing), the in-process inbound queue + bounded worker pool, calendar-as-cron-producer, and how each fixes the four §1.6 violations.
3. **Do NOT implement yet unless the user says so** — this has been planning-only. The user is switching agents; confirm scope before touching the live box or opening code PRs.
4. If implementing later: Shape 1 first (in-process queue, strip bespoke DeepSeek triage, add `account_id`), verify against a couple of channels, then migrate to Shape 2 multi-instance adapters.

## 7. Constraints to respect (from repo AGENTS.md)
- Prompt-cache safety: system prompt must stay byte-stable within a conversation.
- Footprint ladder: new capability = extend → CLI+skill → service-gated tool → plugin → MCP → (last resort) new core tool. Never grow the core waist casually.
- `.env` = secrets only; all behavioral config in `config.yaml`.
- Subagent limits: children can't clarify/memory/cronjob/recurse (`DELEGATE_BLOCKED_TOOLS`); `skip_memory=True` so they can't read shared memory — orchestrator must pass context in the briefing.
- Any MCP/OSS-integration work: approval-gated + provenance-tracked (design #1 §4).

## 8. Files
- `docs/design/architecture-design-number-one.md` — frozen full plan (design #1).
- `docs/design/AGENT-HANDOFF.md` — this note (design #2 active state).
- `docs/{WHATSAPP,EMAIL,CALENDAR}_IMPLEMENTATION.md` — the existing (standalone) channel implementations that design #2 will connect to the agent loop.
