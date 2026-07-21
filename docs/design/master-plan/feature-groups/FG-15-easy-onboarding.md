# FG-15 — Easy onboarding

**Wave:** B (Phase-2) · **Owner agent:** devin:581cc02e · **Status:** CLI + backend + schema + readiness API implemented (dashboard first-run wizard UI deferred to FG-17); ECS system test pending (Leo-owned)

## Summary
Make first-time setup **very easy and unambiguous**: clearly surface the small
set of **must-have** information needed to bring the system up (and separate it
from the optional/advanced settings), with a guided flow in both the **CLI** and
the **dashboard** (a first-run wizard). The dashboard shows an ongoing
**readiness score** so it is always obvious what's configured and what's still
missing. (Req 15.0.)

## Decisions applied
- Footprint ladder: extend the existing `hermes setup` wizard + `hermes tools`; the dashboard wizard is a UI over the same backend — no new core tool, no new non-secret env vars.
- C1 (owner enrollment is the first onboarding step), C6 (any prompts respect consent), C8 (onboarding steps are traced).

## Reuse map
- `hermes_cli/` setup wizard (`hermes setup`) + `config.yaml` `onboarding:` section (`profile_build`, `seen:` flags already exist) — extend, don't replace.
- `hermes tools` (auto-enable-on-credential) — the pattern for "provide key → capability lights up".
- FG-01 pairing/enrolment (owner + first users), FG-13 datastore router (Supabase DSN), FG-03 channels (bind the first channel), model/provider config (DeepSeek).
- FG-17 dashboard shell — hosts the first-run wizard + readiness widget.

## Design / approach
1. **Define the "must-have" set (the essential 5).** A typed **setup schema**
   marks each item **required** vs **optional**:
   - (R) **Owner identity** — enroll the single owner (C1).
   - (R) **LLM provider secret** — e.g. `DEEPSEEK_API_KEY` (`.env`, secret).
   - (R) **Application datastore** — Supabase `app_*` DSN (C3).
   - (R) **At least one conversational channel** — Telegram (embedded/app) bound.
   - (O) Additional channels (WhatsApp/email), memory provider, extra users, tools.
2. **Guided flow, two front-ends, one backend:** `hermes setup` (CLI) and a
   dashboard **first-run wizard** call the **same** setup backend; each required
   item has a check, a fix action, and a short "why we need this" line.
3. **Readiness score:** a computed `readiness = met_required / total_required`
   (+ optional coverage) surfaced on the dashboard and via `hermes status`;
   the system refuses to mark itself "ready for prod" until all **required**
   items are met (ties into FG-13 promotion).
4. **Secrets vs behaviour:** required **secrets** go to `.env` via the existing
   secret flow; everything behavioural stays in `config.yaml` (no new
   `HERMES_*` non-secret vars).
5. **Idempotent + resumable:** re-running setup shows current state, never
   clobbers existing config; each completed step recorded (reuses the existing
   `onboarding.seen` flags) and traced (C8).

## Data model
- `config.yaml` `onboarding:` (extended: required-item completion flags) — behavioural, not secret.
- Optional `app_*` `onboarding_state(user_id, item, status, ts)` for the dashboard wizard's per-owner progress; reused C8 trace for step events.

## Dev/Prod + Supabase
Setup runs in dev/staging first; the readiness gate is what a promotion (FG-13)
checks. DSN/secret entry uses the secret flow (never printed/committed).

## Testing requirements
- Unit: setup schema (required vs optional), readiness computation, idempotent re-run (no clobber).
- E2E: fresh temp `HERMES_HOME` → run guided setup → each required item flips to met → readiness = 100% required; missing a required item keeps readiness < 100% and blocks the "ready" state.
- No new non-secret env vars (assert secrets→`.env`, behaviour→`config.yaml`).
- Baseline green.

## System testing (system-test box)
**Required after this FG's development completes** (part of DoD), on the new ECS (`hermes-systest`, 4/16, EIP `47.83.199.25`) against **staging** (`app_dev`) (**never prod**). See README §7.1. Checklist:
- From a near-empty staging config on the box, complete the **first-run wizard** end-to-end (owner, DeepSeek secret, Supabase DSN, one Telegram channel) and confirm readiness reaches 100% required.
- Confirm the dashboard readiness widget + `hermes status` agree, and that leaving a required item blank keeps the system out of "ready".
- **Gate:** not complete/promotable until this checklist passes.

## Dependencies
- **Blocked by:** FG-01 (owner enrol), FG-13 (DSN/mode). Dashboard wizard rides FG-17 (CLI flow can ship first).
- **Blocks:** nothing hard; improves every other FG's out-of-box experience.

## Definition of Done
Tests green + baseline green + `ruff`/`ty` (+ web lint/typecheck for the wizard) clean; required-vs-optional schema + readiness score implemented; CLI + dashboard share one backend; secrets→`.env`, behaviour→`config.yaml`; **ECS system test green**.

## Progress checklist
- [x] Typed setup schema (required vs optional; the essential 5) — `hermes_cli/onboarding_readiness.py`
- [x] Guided flow: extend `hermes setup` (CLI) + one shared backend — new `hermes setup essentials` section (check + fix + rationale per required item); dashboard first-run wizard **UI deferred to FG-17** (consumes the same backend)
- [x] Readiness score (`hermes status` + `GET /api/onboarding/readiness`) + prod-ready gate — dashboard widget UI deferred to FG-17
- [x] Idempotent/resumable (reuse `onboarding.seen`); secrets→`.env`, behaviour→`config.yaml`; traced via C8
- [x] tests (unit + E2E + no-new-env) green
- [ ] System test on the system-test ECS passed (see *System testing* section) — **owned by Leo; not run in this session**

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-12 | 1 | devin:8cec0d47 (for Leo) | Created FG doc | Phase-2 req 15.0: onboarding must be very easy; clearly surface the must-have setup info |
| 2026-07-12 | 2 | devin:581cc02e (for Leo) | Implemented CLI + backend + schema + readiness API (typed setup schema, `hermes setup essentials`, readiness on `hermes status`, `GET /api/onboarding/readiness`, prod gate, C8 trace, unit+E2E+no-new-env tests) | Deliver req 15.0 CLI/backend scope; dashboard first-run wizard UI deferred to FG-17; ECS system test + prod promotion remain Leo-owned gated steps |

## Cloud-agent prompt
> **[Phase-2 Wave B — start after FG-01/FG-13 merged; dashboard wizard after FG-17]** Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-15`). Make onboarding **very easy**: define a typed **setup schema** that marks each item **required** vs **optional** (the essential 5 required: owner identity (C1), LLM provider secret e.g. `DEEPSEEK_API_KEY`, Supabase `app_*` DSN (C3), at least one Telegram channel; optional: more channels/users/tools/memory). Extend the existing `hermes setup` wizard AND add a **dashboard first-run wizard** that call the SAME backend; each required item has a check + fix action + one-line rationale. Add a computed **readiness score** surfaced on the dashboard and `hermes status`, and gate the "ready for prod" state on all required items met. Secrets go to `.env` via the existing secret flow; **all behavioural setup stays in `config.yaml`** (no new `HERMES_*` non-secret vars). Idempotent/resumable (reuse `onboarding.seen`), traced via C8. Follow `AGENTS.md` (extend, don't duplicate; footprint ladder). Add unit + E2E (temp `HERMES_HOME`) + no-new-env tests + web lint/typecheck; keep baseline green; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist passes** — coordinate with Leo.
