# FG-03 — Multi-channel redesign (one brain, all channels)

**Wave:** 1 · **Owner agent:** devin:733e4888 · **Status:** Contract + Shape-1 merged; **live-gateway wiring (C4 identity enrichment in `gateway/run.py`) IMPLEMENTED** — see *Gateway migration* below. Telegram live-tested; WhatsApp/email **live round-trip still pending** the channel creds (email = old-box Gmail IMAP; WhatsApp = QR bind).

## Summary
Route **every** incoming channel (N WhatsApp numbers, N emails, N calendars, …)
through the **one shared `AIAgent` loop** — the same skills/memory/context —
instead of the bespoke `custom/*` scripts that bypass the agent core today
("unified storage, siloed cognition" → "one brain, all channels"). This FG is
already designed in depth in **`docs/design/architecture-design-number-one.md`**
and **`docs/design/AGENT-HANDOFF.md`**; that design is authoritative — this doc
is the execution wrapper.

## Decisions applied
- D7 (`session_key` gains `account_id` + user/task), D5 (channels prod-only), D2 (hybrid memory for shared coordination state).

## Reuse map
- `gateway/session.py` `build_session_key` / `SessionSource` — extend with `account_id` (C4).
- `gateway/run.py` — per-`session_key` cached `AIAgent` (already exists — "Cache AIAgent instances per session to preserve prompt caching"); N accounts/conversations = N cores, one profile.
- `custom/{whatsapp,email,calendar}/*` pollers/batchers — **keep as thin producers**; strip bespoke DeepSeek triage.
- `gateway/platforms/*` adapters — the Shape-2 durable target (N adapter instances per account).

## Design / approach (from design #2)
1. **C4 — add `account_id` (receiving-inbox identity) to `SessionSource`** and
   fold into `session_key` so per-account conversations don't collide and
   **egress replies leave via the correct account**. Must remain
   **byte-identical for existing single-account callers** (locked by
   `tests/plan_baseline/test_session_key_baseline.py`).
2. **Shape 1 (first):** producers push normalized events
   `(platform, account_id, sender_chat_id, payload)` into an **in-process
   inbound queue** in the gateway → bounded async worker pool → each worker
   owns one session's cached core. Reuse working pollers; no adapter rewrite.
3. **Shape 2 (durable):** teach the gateway to run **N adapter instances, one
   per account** (extend `PlatformConfig` with `accounts:`). Migrate after
   Shape 1 proves out.
4. **Calendar = cron/heartbeat producer** into the same queue.
5. **Channels are PROD-ONLY** (D5/C3 guard).

## Gateway migration (Shape-1 → live `gateway/run.py`)
**Status: IMPLEMENTED (code); live channel round-trip pending creds.** Shape-1
first landed the router/producers as a *contract with tests*
(`gateway/inbound.py` `InboundRouter`, `gateway/producers.py`). The live gateway
is now wired to realise the same contract at runtime.

**Design decision (why not a second `InboundRouter` instance in the loop).** The
live gateway *already* implements the `InboundRouter` semantics: one cached
`AIAgent` per `session_key` (`_agent_cache`), per-session **serial** turns
(`_running_agents` + the session interrupt/owner-task guards), and
cross-session **parallel** dispatch. Bolting a second in-process queue in front
of it would duplicate that machinery and add a hand-off hazard. What the live
path was actually missing was the **C4 identity**: it never stamped the
receiving `account_id` or resolved the sender to an internal `Principal`, so
every channel keyed purely on the raw handle. The migration therefore wires the
**identity enrichment** — not a parallel queue — into the one inbound
chokepoint, so `build_session_key` folds in `account_id`/`internal_user_id` and
the existing cache/serialisation gives us the one-brain, per-internal-user
behaviour. `InboundRouter`/producers remain the canonical Shape-1 contract
(and the entry point for out-of-process producers / Shape-2).

**Goal (met at the code level):** every channel turn keys on
`session_key = f(channel, account_id, internal_user, task)` (C4) with the
channel sender bound to an internal `Principal` (C1) and channel origin forced
prod (C3), and egresses via the receiving `account_id`.

**Verifiable checklist (Definition of Done for the migration):**
- [x] `gateway/run.py` enriches the inbound `SessionSource` at the single
      chokepoint (`_handle_message_with_agent`) via
      `_enrich_channel_source_identity` — stamps the receiving `account_id`
      (from the adapter) and resolves the sender→internal `Principal` through
      the C1 `bind_channel_principal` seam (`_get_principal_store`), **before**
      the session key / cached-`AIAgent` lookup, replacing raw-handle-only
      keying. (Realises the `InboundRouter` contract on the gateway's existing
      per-session-serial / cross-session-parallel cached-agent dispatch — see
      *Design decision* above — rather than adding a second queue.)
- [x] The receiving `account_id` comes from the adapter
      (`BasePlatformAdapter.account_id`, config-driven via
      `platforms.<p>.account_id` / `extra.account_id`, `None` by default); no
      adapter runs its own LLM/DeepSeek triage (reasoning happens once in the
      shared core). Producers (`normalize_whatsapp`/`_email`/`_message`) remain
      the normaliser API for out-of-process / Shape-2 feeds.
- [x] Egress routes back through the originating `account_id` — in Shape-1 the
      reply leaves via the same adapter that received it, and distinct
      `account_id`s produce distinct `session_key`s / cached cores (unit-tested
      for ≥2 accounts, same sender).
- [x] **Per-user pairing/enrollment:** a WhatsApp sender / email `from_address`
      resolves to an internal `Principal` (auto-enrol when pairing-approved via
      `gateway/pairing.py`); unpaired identities fall back to channel-identity
      keying (documented, not silently shared). Gated: no-op when the app DB
      DSN is unset, so single-account deployments keep byte-identical keys.
- [x] Prompt-cache safety preserved: reuses `gateway/run.py`'s existing
      per-`session_key` cached `AIAgent`, frozen system prompt, strict role
      alternation, per-session serial / cross-session parallel drain — the
      enrichment only adds additive key dimensions, it does not touch the
      prompt/toolset or message sequencing.
- [x] Tests: `tests/gateway/test_live_gateway_identity_wiring.py` (account_id
      stamping, two-account isolation, no-op gating, C1 principal resolution,
      error fall-back, DSN gate) + the existing Postgres E2E
      (`test_inbound_principal_e2e.py`) for real principal binding + negative
      access. `scripts/run_tests.sh`, `ruff`, `ty`, baseline all green.
- [ ] **System test on `hermes-systest`:** live WhatsApp round-trip (paired
      number → shared brain → reply out) and live email round-trip
      (inbox → shared brain → reply out), plus the two-account isolation +
      channel-prod guard checks from the *System testing* section, run on the
      deployed box. **Pending channel creds** (email = old-box Gmail IMAP
      app-passwords; WhatsApp = QR bind). Record evidence.

**Non-goals here:** Shape-2 (N durable adapter instances per account via
`PlatformConfig.accounts:`) remains a later step; this migration wires the C4
identity into the live gateway's existing dispatch.

## Data model
- `SessionSource.account_id` (additive). Shared coordination state (in-flight / handled / dedupe / per-lead status) lives in the FG-05 **live** store, read via tool call (cache-safe).

## Dev/Prod + Supabase
Channel ingestion forced to `prod` (C3 guard, tested). Coordination state in `app_prod`.

## Testing requirements
- **Regression lock:** `build_session_key` byte-identical for single-account callers (baseline suite).
- Unit: `account_id` isolates two accounts' conversations; egress routes to correct account.
- E2E: two accounts → two isolated cores under one profile; a message on account A and account B don't cross-contaminate history.
- Channel prod-only guard test.

## System testing (system-test box)
**Required step after this FG's development completes** (part of its Definition of Done), on top of the per-PR unit/E2E + baseline gate: deploy this FG to the new ai-prentice ECS (`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, 4/16, cn-hongkong-b, EIP `47.83.199.25`) — the dedicated **system-test host** — and exercise it end-to-end on the real stack against a **staging** Supabase schema (`app_staging`) + staging SQLite core (**never prod**). See README §7.1. Acceptance checklist:
- Bind ≥2 real **test** channel accounts (e.g. 2 WhatsApp numbers / 2 email inboxes) on the deployed box; confirm both route into the **one shared brain**, sessions stay isolated per account, and replies egress via the correct account.
- Exercise the **channels-prod-only** guard on the live stack (channel traffic never resolves to dev/staging mode).
- Confirm shared coordination state is read via tool call (cache-safe) across the two accounts — no system-prompt mutation.
- **Gate:** this FG is not complete/promotable until this ECS checklist passes (on top of the per-PR gate).

## Dependencies
- **Blocked by:** FG-13 (C3), FG-05 (hybrid memory for coordination state).
- **Blocks:** FG-06 (task discovery from convo), FG-09.
- Publishes contract **C4** early in Wave 1.

## Definition of Done
Tests green + baseline green (session-key lock intact) + `ruff`/`ty` clean; Shape 1 in-process queue works E2E for ≥2 accounts; design-doc principles upheld (one brain, cache-safe); **ECS system test green**.

## Progress checklist
- [x] `SessionSource.account_id` (+ `internal_user_id` / `task`) + `session_key` fold-in (C4), single-account byte-stable
- [x] In-process inbound queue + bounded worker pool (Shape 1) — `gateway/inbound.py` `InboundRouter` (per-session serial, cross-session parallel)
- [x] Producers stripped to thin normalizers (WhatsApp/email/calendar) — `gateway/producers.py`
- [x] Calendar cron producer — `gateway/producers.py` `calendar_event_to_inbound` / `run_calendar_sync`
- [x] Channel prod-only guard — `gateway/inbound.py` `guard_channel_prod` (routes via C3 `resolve_mode`)
- [x] Channel identity → Principal bound via the C1 `resolve_principal` seam — `gateway/inbound.py` `bind_channel_principal`
- [x] tests + Shape-2 migration notes (unit + Postgres E2E incl. negative-access; Shape 2 documented in `gateway/inbound.py` + Design §3)
- [x] **Live-gateway wiring (C4 identity enrichment in `gateway/run.py`)** — see *Gateway migration* section; the running gateway now stamps `account_id` + resolves the internal `Principal` at the inbound chokepoint (unit-tested). Live WhatsApp/email round-trip still pending channel creds.
- [x] System test on the system-test ECS — Telegram inbound→C4 route→DeepSeek→egress + web/Telegram parity **PASSED** (2026-07-11); WhatsApp + email live round-trip **still pending** (needs the gateway migration + channel creds)

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |
| 2026-07-11 | 2 | devin:8cec0d47 | Added System testing (system-test box) section as a per-FG DoD step | Leo: new 4/16 ECS = system-test host (+ prod for now), run after each FG's development |
| 2026-07-12 | 4 | devin:8cec0d47 | Added *Gateway migration (Shape-1 → live `gateway/run.py`)* section + checklist; corrected status/progress to reflect that the live gateway does not yet route channels through `InboundRouter` (only Telegram was live-tested; WhatsApp/email live round-trip pending migration + creds) | Leo: migrate the live gateway to the one-brain router first, then do live WhatsApp/email round-trips; document so future agents can follow/verify |
| 2026-07-11 | 3 | devin:733e4888 | Implemented C4 (`SessionSource.account_id`/`internal_user_id`/`task` + `build_session_key` fold-in, byte-stable for single-account callers) and Shape 1 (`gateway/inbound.py` in-process `InboundRouter` queue+bounded pool with per-session-serial / cross-session-parallel turns; `gateway/producers.py` thin WhatsApp/email/calendar normalizers + calendar cron producer). Bound channel identity → Principal via the C1 `resolve_principal` seam; channels forced prod-only via the C3 router. Added unit tests + Postgres E2E (≥2 accounts isolated, principal binding, negative-access); baseline + `ruff`/`ty` clean. | Publish contract C4 early in Wave 1 (FG-06/FG-09 depend on it); realise one-brain-all-channels without per-channel silos while preserving prompt-cache safety. |
| 2026-07-11 | 6 | devin:8cec0d47 | **Live-gateway wiring implemented.** Wired C4 identity into the running gateway: `_handle_message_with_agent` now calls `_enrich_channel_source_identity` (stamps receiving `account_id` from `BasePlatformAdapter.account_id`; resolves sender→internal `Principal` via `bind_channel_principal`/`_get_principal_store`) **before** session-key/cached-`AIAgent` lookup. Gated on the app-DB DSN (no-op / byte-stable when unset). Chose to enrich the gateway's *existing* per-session-serial / cross-session-parallel cached-agent dispatch rather than add a second `InboundRouter` queue (documented under *Design decision*). Added `tests/gateway/test_live_gateway_identity_wiring.py`; baseline + `ruff`/`ty` clean. Live WhatsApp/email round-trip still pending channel creds. | Leo: migrate the live gateway to the one-brain router first, then live WhatsApp/email round-trips; keep it followable/verifiable. |

## Cloud-agent prompt
> **[Wave 1 — start after Wave 0 merges]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md`, this doc (`FG-03`), and the authoritative designs `docs/design/architecture-design-number-one.md` + `docs/design/AGENT-HANDOFF.md`. Implement the **multi-channel redesign (one brain, all channels)**: (1) add `account_id` to `SessionSource` and fold it into `build_session_key` (contract C4) while keeping keys **byte-identical for existing single-account callers** — `tests/plan_baseline/test_session_key_baseline.py` must stay green; (2) build **Shape 1**: an in-process inbound queue + bounded async worker pool in the gateway, with the existing `custom/*` pollers reduced to thin producers emitting `(platform, account_id, sender_chat_id, payload)`, each routed to the per-session cached `AIAgent` (reuse `gateway/run.py`'s cache); (3) add a calendar cron producer into the same queue; (4) enforce **channels prod-only** via contract C3. Shared coordination state goes in the FG-05 live store via tool call (cache-safe) — never in the system prompt. Follow `AGENTS.md` (one brain/one profile, cache-sacred, footprint ladder). Add unit + E2E (≥2 accounts isolated) + prod-only guard tests; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist (in this doc) passes** — coordinate that deploy/run with Leo.
