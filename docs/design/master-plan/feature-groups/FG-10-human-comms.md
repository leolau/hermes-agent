# FG-10 — Human communications: Telegram + web app

**Wave:** 2 · **Owner agent:** _unassigned_ · **Status:** Not started

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

## Dependencies
- **Blocked by:** FG-01 (C1/C2), FG-12 (C6 + change log), FG-03 (`account_id` routing).
- **Blocks:** FG-09 (human surface for goal mgmt).
- Co-publishes **C6** with FG-12.

## Definition of Done
Tests green + baseline green + `ruff`/`ty` + web lint/typecheck clean; Telegram + web parity for messaging/approvals/change-review; C6 enforced; cache-safe; `data-component` applied.

## Progress checklist
- [ ] Principal-aware messaging + reply routing (account_id)
- [ ] Web app: chat, scoped goals/tasks/memory, approvals, change review
- [ ] C6 quiet-hours/rate-limit/consent enforcement + dedupe across surfaces
- [ ] tests (unit + negative + E2E) green

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |

## Cloud-agent prompt
> **[Wave 2 — start after FG-01 + FG-12 + FG-03 merge]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-10`). Deliver **human comms parity across Telegram + web app** for the multi-user world: resolve every human message to a `Principal` (contract C1) and route replies to the correct channel/account (FG-03 `account_id`); extend the `web/` app (`hermes_cli/web_server.py`) so users can chat, view **scope-filtered** (contract C2) goals/tasks/memory, approve/deny pending actions, and review + undo/redo changes (FG-12) and manage tools (FG-07). All approvals + proactive asks (4.1/6.1) ride the shared **quiet-hours/rate-limit/consent policy (contract C6, co-owned with FG-12)** and are delivered as **appended messages** (never system-prompt mutations); de-duplicate a pending item across Telegram + web (answering one clears the other). New web components carry `data-component="ComponentName"`. Follow `AGENTS.md` (cache-sacred, footprint ladder, config not env). Add unit + negative-access + E2E tests + web lint/typecheck; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc.
