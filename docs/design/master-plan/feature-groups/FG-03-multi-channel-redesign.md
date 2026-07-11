# FG-03 — Multi-channel redesign (one brain, all channels)

**Wave:** 1 · **Owner agent:** _unassigned_ · **Status:** Not started

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

## Data model
- `SessionSource.account_id` (additive). Shared coordination state (in-flight / handled / dedupe / per-lead status) lives in the FG-05 **live** store, read via tool call (cache-safe).

## Dev/Prod + Supabase
Channel ingestion forced to `prod` (C3 guard, tested). Coordination state in `app_prod`.

## Testing requirements
- **Regression lock:** `build_session_key` byte-identical for single-account callers (baseline suite).
- Unit: `account_id` isolates two accounts' conversations; egress routes to correct account.
- E2E: two accounts → two isolated cores under one profile; a message on account A and account B don't cross-contaminate history.
- Channel prod-only guard test.

## System testing (existing ECS)
**Required step after this FG's development completes** (part of its Definition of Done), on top of the per-PR unit/E2E + baseline gate: deploy this FG to the existing ai-prentice ECS (`i-j6camnt3ocwlmzajthil`, 2/4, cn-hongkong) — the dedicated **system-test host** — and exercise it end-to-end on the real stack against a **staging** Supabase schema (`app_staging`) + staging SQLite core (**never prod**). See README §7.1. Acceptance checklist:
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
- [ ] `SessionSource.account_id` + `session_key` fold-in (C4), single-account byte-stable
- [ ] In-process inbound queue + bounded worker pool (Shape 1)
- [ ] Producers stripped to thin normalizers (WhatsApp/email/calendar)
- [ ] Calendar cron producer
- [ ] Channel prod-only guard
- [ ] tests + Shape-2 migration notes
- [ ] System test on existing ECS passed (see *System testing* section)

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |
| 2026-07-11 | 2 | devin:8cec0d47 | Added System testing (existing ECS) section as a per-FG DoD step | Leo: existing ECS = system-test host, run after each FG's development |

## Cloud-agent prompt
> **[Wave 1 — start after Wave 0 merges]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md`, this doc (`FG-03`), and the authoritative designs `docs/design/architecture-design-number-one.md` + `docs/design/AGENT-HANDOFF.md`. Implement the **multi-channel redesign (one brain, all channels)**: (1) add `account_id` to `SessionSource` and fold it into `build_session_key` (contract C4) while keeping keys **byte-identical for existing single-account callers** — `tests/plan_baseline/test_session_key_baseline.py` must stay green; (2) build **Shape 1**: an in-process inbound queue + bounded async worker pool in the gateway, with the existing `custom/*` pollers reduced to thin producers emitting `(platform, account_id, sender_chat_id, payload)`, each routed to the per-session cached `AIAgent` (reuse `gateway/run.py`'s cache); (3) add a calendar cron producer into the same queue; (4) enforce **channels prod-only** via contract C3. Shared coordination state goes in the FG-05 live store via tool call (cache-safe) — never in the system prompt. Follow `AGENTS.md` (one brain/one profile, cache-sacred, footprint ladder). Add unit + E2E (≥2 accounts isolated) + prod-only guard tests; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (existing ECS)* checklist (in this doc) passes** — coordinate that deploy/run with Leo.
