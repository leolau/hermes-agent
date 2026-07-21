# FG-13 — Dev vs Prod mode + dev SQLite/Supabase (channels prod-only)

**Wave:** 0 (foundation) · **Owner agent:** _unassigned_ · **Status:** Implemented (C3 dev/prod datastore routing + promotion, `hermes_cli/datastore.py`) — merged to `develop` (PR #9); ECS system-test remains owner-gated

## Summary
Introduce a first-class **mode** (`dev` | `prod`) for everything users/agents
build (tools, skills, config, in-house systems, app data). New work is created
in **dev**, validated, then **promoted to prod** on explicit confirmation.
**Incoming channels are PROD-ONLY** — there is no dev channel ingestion (D5).

## Decisions applied
- D4 (SQLite core + Supabase app layer), D5 (dev→prod promotion, channels prod-only), D3 (in-house/remote systems consume mode).

## Reuse map (extend, don't reinvent)
- `hermes_constants.get_hermes_home()` / `get_hermes_dir()` — profile-aware paths; add mode-aware sub-paths for the **core SQLite** dev copy.
- `hermes_state.py` `DEFAULT_DB_PATH` + WAL fallback — the pattern for a parallel **dev** SQLite DB (`state.dev.db`) alongside prod.
- `config.yaml` layering — add a `mode` + per-mode overrides section (NOT new `HERMES_*` env vars).
- Supabase: two databases/schemas — `app_prod` and `app_dev` (Postgres schema or separate DB), migrations applied to both.

## Design / approach
1. **Contract C3 — datastore router** (this FG publishes it): a single accessor
   `get_store(kind: "sqlite-core" | "supabase-app", mode: "dev" | "prod")` that
   every DB-touching capability calls. No module opens a raw connection
   directly anymore for the new app layer.
2. **Mode resolution:** default `prod`; a session/CLI/dashboard toggle sets
   `dev` for authoring flows. Channel-originated sessions are **forced to
   `prod`** at `resolve` time (hard guard + test).
3. **Promotion pipeline:** `hermes promote <artifact>` (CLI+skill rung) moves a
   tool/skill/config/schema-migration from dev→prod with an **approval** (C6)
   and a **change-event** (C5). Promotion moves *definitions/config/schema*,
   **not** raw prod data.
4. **Supabase dev/prod:** migrations are authored once and applied to both
   schemas; RLS policies identical. In-house tools (FG-08) read their mode from
   C3.

## Data model
- Core SQLite: `state.db` (prod) + `state.dev.db` (dev) — dev is disposable.
- Supabase: schemas `app_prod`, `app_dev`; a `promotions(id, artifact_kind, artifact_ref, from_mode, to_mode, approval_ref, change_ref, ts, actor)` audit table.

## Dev/Prod + Supabase notes
This FG *defines* the dev/prod semantics all other FGs consume. Keep the seam
tiny and typed.

## Testing requirements
- Unit: mode resolution defaults to `prod`; explicit `dev` honoured.
- **Hard guard test:** a channel-originated `SessionSource` can never resolve to `dev` (negative test).
- E2E: create an artifact in dev, promote to prod, assert prod store now has the definition and an approval + change-event + promotion row exist; dev store untouched by prod reads.
- Baseline suite stays green.

## System testing (system-test box)
**Required step after this FG's development completes** (part of its Definition of Done), on top of the per-PR unit/E2E + baseline gate: deploy this FG to the new ai-prentice ECS (`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, 4/16, cn-hongkong-b, EIP `47.83.199.25`) — the dedicated **system-test host** — and exercise it end-to-end on the real stack against a **staging** Supabase schema (`app_staging`) + staging SQLite core (**never prod**). See README §7.1. Acceptance checklist:
- On the ECS: exercise the dev→prod promotion pipeline for a config/schema change (approval + change-event + promotion); confirm dev (`state.dev.db`/`app_dev`) and prod (`state.db`/`app_prod`) stores are separated.
- Confirm **channel-originated sessions are forced to prod** on the deployed box (hard guard), and that promotion moves definitions/schema, **not** prod data.
- **Gate:** this FG is not complete/promotable until this ECS checklist passes (on top of the per-PR gate).

## Dependencies
- **Blocked by:** none (Wave 0 root).
- **Blocks:** FG-03 (channels prod-only), FG-04/06/07/08/09/12 (all consume C3), FG-05 (Supabase mode).
- Co-owns contracts **C3** (publish), touches C5/C6 at promotion.

## Definition of Done
New tests green + baseline green + `ruff`/`ty` clean; C3 documented with typed interface + docstrings; promotion CLI works E2E on a temp `HERMES_HOME` + throwaway Postgres schema; **ECS system test green**.

## Progress checklist
- [x] C3 datastore router interface + docs
- [x] Mode resolution + channel prod-only guard
- [x] dev SQLite parallel DB
- [x] Supabase app_dev/app_prod schemas + migration runner
- [x] `hermes promote` CLI + skill
- [ ] tests (unit + guard + E2E) green
- [ ] System test on the system-test ECS passed (see *System testing* section)

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |
| 2026-07-11 | 2 | devin:8cec0d47 | Added System testing (system-test box) section as a per-FG DoD step | Leo: new 4/16 ECS = system-test host (+ prod for now), run after each FG's development |
| 2026-07-11 | 3 | devin:0520a5a7 | Implemented C3 routing, channel prod guard, mode-separated stores, and approval-gated promotion | Publish the Wave-0 datastore seam and dev-to-prod workflow; ECS system test remains owner-coordinated |

## Cloud-agent prompt
> **[Wave 0 — start immediately]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-13`). Implement the **dev/prod mode + datastore router (contract C3)**: a single typed accessor `get_store(kind, mode)` for `sqlite-core` (prod `state.db` + disposable `state.dev.db`) and `supabase-app` (`app_prod`/`app_dev` schemas); mode defaults to `prod`; **channel-originated sessions are forced to prod (hard guard + negative test)**; add a `hermes promote` CLI+skill that moves definitions/config/schema dev→prod behind an approval (contract C6) emitting a change-event (contract C5). Follow `AGENTS.md`: no new `HERMES_*` env vars (config in `config.yaml`), footprint ladder, no core-tool growth. Add unit + E2E tests against a temp `HERMES_HOME` + throwaway Postgres schema; keep `tests/plan_baseline/` green; run `scripts/run_tests.sh`, `ruff`, `ty`. Update ONLY this FG doc's progress checklist + audit log. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist (in this doc) passes** — coordinate that deploy/run with Leo.
