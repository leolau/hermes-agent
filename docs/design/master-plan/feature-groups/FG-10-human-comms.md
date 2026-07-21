# FG-10 — Human communications: Telegram + web app

**Wave:** 2 · **Owner agent:** _unassigned_ · **Status:** Implemented (human-comms parity across Telegram + web) — merged to `develop` (PR #19); ECS system-test + prod promotion remain owner-gated

## Summary
Give humans **two first-class control/communication surfaces** — **Telegram**
and the **web app** — with parity for the multi-user world: identity-aware
messaging, approvals, proactive asks (4.1/6.1), and change review (12), all
respecting the shared consent/quiet-hours policy (C6) and cache-safety.

## Decisions applied
- D1/C1 (per-user identity across channels), C6 (approval/consent — co-published with FG-12), cache-safety (appended messages only).

## Reuse map
- `gateway/platforms/telegram*` — Telegram adapter; `gateway/authz_mixin.py` + `gateway/pairing.py` for per-user auth.
- `tools/approval.py` / `clarify_gateway` / `write_approval.py` — human-in-the-loop prompts (→ C6).
- `web/` + `hermes_cli/web_server.py` — the web app (shared with FG-07 dashboard).
- FG-12 change log; FG-04/06 proactive asks.

## Design / approach
1. **Identity-aware messaging:** every human message resolves to a `Principal`
   (C1); replies route to the right channel/account (FG-03 `account_id`).
2. **Web app parity:** chat with the agent, see goals/tasks/memory (scoped by
   C2), approve/deny pending actions, review + undo/redo changes (FG-12),
   manage tools (FG-07). New components carry `data-component`.
3. **Approvals + proactive asks** ride **C6** (quiet-hours, rate-limit,
   consent). All agent-initiated messages are **appended** (never system-prompt
   mutations).
4. **Notifications:** pending approvals / proactive questions delivered to the
   user's preferred surface (Telegram and/or web), de-duplicated.

## Dev/Prod + Supabase
Human comms operate in prod for channels; web app can toggle dev sessions for
authoring (C3). Consent/quiet-hours prefs in `app_*` per principal.

## Testing requirements
- Unit: principal-aware routing; C6 quiet-hours/rate-limit enforcement; dedupe.
- Negative access: web app shows only what the principal may see (C2); owner sees all.
- E2E: pending approval appears in Telegram + web; responding in one clears the other; a proactive ask respects quiet-hours.
- Baseline green.

## System testing (system-test box)
**Required step after this FG's development completes** (part of its Definition of Done), on top of the per-PR unit/E2E + baseline gate: deploy this FG to the new ai-prentice-4-all ECS (`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, 4/16, cn-hongkong-b, EIP `47.83.199.25`) — the dedicated **system-test host** — and exercise it end-to-end on the real stack against a **staging** Supabase schema (`app_staging`) + staging SQLite core (**never prod**). See README §7.1. Acceptance checklist:
- On the deployed box: a pending approval appears in **real Telegram + the web app**; responding in one clears it in the other; a proactive ask respects quiet-hours (C6).
- Each principal sees only **scope-filtered** (C2) goals/tasks/memory in both surfaces; owner sees all.
- **Gate:** this FG is not complete/promotable until this ECS checklist passes (on top of the per-PR gate).

## Dependencies
- **Blocked by:** FG-01 (C1/C2), FG-12 (C6 + change log), FG-03 (`account_id` routing).
- **Blocks:** FG-09 (human surface for goal mgmt).
- Co-publishes **C6** with FG-12.

## Definition of Done
Tests green + baseline green + `ruff`/`ty` + web lint/typecheck clean; Telegram + web parity for messaging/approvals/change-review; C6 enforced; cache-safe; `data-component` applied; **ECS system test green**.

## Progress checklist
- [x] Principal-aware messaging + reply routing (account_id) — inbound C1 seam
      reused (`gateway/inbound.bind_channel_principal`); outbound C1-aware
      egress primitive `human_comms.resolve_reply_target` (platform +
      `account_id` + resolved principal), unit-tested.
- [x] Web app: scoped goals/memory + approvals + change review (undo/redo) on
      the existing dashboard chat surface. New `CommsPage` + `/api/comms/*`.
      (Scoped *tasks* deferred — no merged multi-user task-store contract yet;
      the C2 goal registry is the planning surface at this wave.)
- [x] C6 quiet-hours/rate-limit/consent enforcement + dedupe across surfaces —
      shared `NotificationStore` (one Postgres row per pending item); answering
      from either surface settles it idempotently (`newly_answered`).
- [x] tests (unit + negative-access + real-Postgres E2E) green
- [ ] System test on the system-test ECS passed (see *System testing* section)
      — **pending owner (Leo) coordination**; live Telegram delivery + gateway
      egress wiring are exercised there (no ECS/prod access from this PR).

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |
| 2026-07-11 | 2 | devin:8cec0d47 | Added System testing (system-test box) section as a per-FG DoD step | Leo: new 4/16 ECS = system-test host (+ prod for now), run after each FG's development |
| 2026-07-11 | 3 | devin:f88fdad7 | Implemented Wave-2 human-comms parity: shared C6-gated `NotificationStore` (cross-surface dedupe), C1-aware `resolve_reply_target` egress, `/api/comms/*` + web `CommsPage` for scoped goals/memory/approvals/change-review, consuming merged C1–C6/C3 contracts. Unit + negative-access + real-Postgres E2E green; ruff/ty/web clean. | Deliver Telegram+web multi-user parity; ECS system-test left pending owner coordination |

## Cloud-agent prompt
> **[Wave 2 — start after FG-01 + FG-12 + FG-03 merge]** Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-10`). Deliver **human comms parity across Telegram + web app** for the multi-user world: resolve every human message to a `Principal` (contract C1) and route replies to the correct channel/account (FG-03 `account_id`); extend the `web/` app (`hermes_cli/web_server.py`) so users can chat, view **scope-filtered** (contract C2) goals/tasks/memory, approve/deny pending actions, and review + undo/redo changes (FG-12) and manage tools (FG-07). All approvals + proactive asks (4.1/6.1) ride the shared **quiet-hours/rate-limit/consent policy (contract C6, co-owned with FG-12)** and are delivered as **appended messages** (never system-prompt mutations); de-duplicate a pending item across Telegram + web (answering one clears the other). New web components carry `data-component="ComponentName"`. Follow `AGENTS.md` (cache-sacred, footprint ladder, config not env). Add unit + negative-access + E2E tests + web lint/typecheck; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist (in this doc) passes** — coordinate that deploy/run with Leo.
