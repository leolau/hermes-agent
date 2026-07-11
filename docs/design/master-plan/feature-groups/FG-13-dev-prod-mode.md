# FG-13 — Dev vs Prod mode + dev SQLite/Supabase (channels prod-only)

**Wave:** 0 (foundation) · **Owner agent:** _unassigned_ · **Status:** Not started

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

## Dependencies
- **Blocked by:** none (Wave 0 root).
- **Blocks:** FG-03 (channels prod-only), FG-04/06/07/08/09/12 (all consume C3), FG-05 (Supabase mode).
- Co-owns contracts **C3** (publish), touches C5/C6 at promotion.

## Definition of Done
New tests green + baseline green + `ruff`/`ty` clean; C3 documented with typed interface + docstrings; promotion CLI works E2E on a temp `HERMES_HOME` + throwaway Postgres schema.

## Progress checklist
- [ ] C3 datastore router interface + docs
- [ ] Mode resolution + channel prod-only guard
- [ ] dev SQLite parallel DB
- [ ] Supabase app_dev/app_prod schemas + migration runner
- [ ] `hermes promote` CLI + skill
- [ ] tests (unit + guard + E2E) green

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |

## Cloud-agent prompt
> **[Wave 0 — start immediately]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-13`). Implement the **dev/prod mode + datastore router (contract C3)**: a single typed accessor `get_store(kind, mode)` for `sqlite-core` (prod `state.db` + disposable `state.dev.db`) and `supabase-app` (`app_prod`/`app_dev` schemas); mode defaults to `prod`; **channel-originated sessions are forced to prod (hard guard + negative test)**; add a `hermes promote` CLI+skill that moves definitions/config/schema dev→prod behind an approval (contract C6) emitting a change-event (contract C5). Follow `AGENTS.md`: no new `HERMES_*` env vars (config in `config.yaml`), footprint ladder, no core-tool growth. Add unit + E2E tests against a temp `HERMES_HOME` + throwaway Postgres schema; keep `tests/plan_baseline/` green; run `scripts/run_tests.sh`, `ruff`, `ty`. Update ONLY this FG doc's progress checklist + audit log. Open a PR linking this doc.
