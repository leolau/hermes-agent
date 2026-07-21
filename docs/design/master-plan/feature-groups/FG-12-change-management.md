# FG-12 — Change management (data/config/code) + undo/redo/approve/backup

**Wave:** 1 · **Owner agent:** devin:587066e0 · **Status:** In review (PR open; ECS system-test pending owner coordination)

## Summary
A **systematic change-management module**: every change to **data**,
**configuration**, and **source code** is recorded in an append-only log the
user can review, with **undo/redo**, **approval**, and **backup** (12.1). One
documented exception: **ERC-721 mint is irreversible** (D6) — it is recorded but
not undoable.

## Decisions applied
- D6 (ERC-721 mint = irreversible exception), C5 (change-event schema — published here), C6 (approval policy — published here jointly with FG-10).

## Reuse map
- `tools/checkpoint_manager.py` — git-shadow per-turn snapshots + `restore(working_dir, commit_hash, file_path)`. **This is the undo engine for source code / files.** Don't reinvent.
- `tools/approval.py` + `tools/write_approval.py` — approval backbone (→ C6).
- `hermes_cli/backup.py` (+ existing backup paths) — backup engine.
- SQLite core + Supabase `app_*` — targets of data/config changes; changes recorded uniformly.

## Design / approach
1. **C5 — append-only change log** (published here):
   `changes(id, ts, actor_user_id, mode, target_kind ∈ {data, config, code},
   op, inverse_op | null, reversible bool, approval_ref, backup_ref, payload)`.
   Every mutating capability (memory writes, config edits, tool changes, code
   edits, promotions) emits one event.
2. **Undo/redo:**
   - *Code/files:* delegate to `checkpoint_manager.restore` (git-shadow).
   - *Config:* inverse-op replay from the recorded prior value.
   - *Data:* inverse-op where reversible (soft-delete/restore, value swap);
     rows with `reversible=false` (e.g. ERC-721 mint) surface as **not undoable**.
   A redo stack mirrors undo.
3. **C6 — approval/consent policy** (published here jointly with FG-10):
   one surface wrapping `approval`/`write_approval` with quiet-hours,
   rate-limit, and consent, used by proactive messaging (4.1/6.1), change
   approvals, and action gating.
4. **Backup:** snapshot before risky changes; `backup_ref` linked from the
   change event; restore path documented.
5. **Review UI:** dashboard view of the change log (FG-07/10 render it).

## Data model
- `changes` (C5) in `app_*` for cross-user visibility (scoped by C2; owner sees all); code changes reference checkpoint commit hashes.
- `redo_stack` derived from `changes` (or a companion table).

## Dev/Prod + Supabase
Changes carry `mode`. Promotions (FG-13) emit change events. Channels (prod)
still log changes.

## Testing requirements
- Unit: change-event emission for each target_kind; inverse-op correctness; redo after undo.
- **Irreversibility test:** an ERC-721-mint change is recorded with `reversible=false` and undo refuses it.
- E2E: edit a file → change logged → undo restores via checkpoint → redo re-applies; config change round-trips; backup_ref restorable.
- Negative access: non-owner can't undo another user's private change; owner can.

## System testing (system-test box)
**Required step after this FG's development completes** (part of its Definition of Done), on top of the per-PR unit/E2E + baseline gate: deploy this FG to the new ai-prentice-4-all ECS (`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, 4/16, cn-hongkong-b, EIP `47.83.199.25`) — the dedicated **system-test host** — and exercise it end-to-end on the real stack against a **staging** Supabase schema (`app_staging`) + staging SQLite core (**never prod**). See README §7.1. Acceptance checklist:
- On real data/config/code changes on the box: confirm each is recorded in the change log; **undo/redo** works (code via checkpoint restore, config/data via inverse-op); a backup is restorable.
- Confirm the ERC-721 mint change is `reversible=false` and undo refuses it.
- **Gate:** this FG is not complete/promotable until this ECS checklist passes (on top of the per-PR gate).

## Dependencies
- **Blocked by:** FG-13 (C3), FG-01 (C2 actor/scope).
- **Blocks:** everything mutating (they emit C5) + FG-07/10 (render log) + FG-02 (marks mint irreversible).
- Publishes **C5, C6**.

## Definition of Done
Tests green (incl. irreversibility) + baseline green + `ruff`/`ty` clean; C5/C6 documented; undo/redo works for code/config/data via reused engines; ERC-721 exception honoured; **ECS system test green**.

## Progress checklist
- [x] C5 append-only change log + emission hooks (`hermes_cli/changes.py`: `initialize_changes` extends the existing `app_prod.changes` additively with `actor_user_id`/`visibility`/`payload`/`undone`; `ChangeLog.record` is the canonical emitter — promotions/owner-transfer already emit C5 rows and stay compatible)
- [x] Undo/redo (code via `checkpoint_manager.restore`, config/data via inverse-op replay) + redo stack (`ChangeLog.undo`/`redo`, `undone`/`undone_at`)
- [x] C6 approval/consent policy (quiet-hours/rate-limit/consent via `config.yaml` `change_management` section, no new env vars) — `hermes_cli/consent.py` wrapping `tools/approval.py`; irreversible ⇒ always prompted
- [x] Backup integration (`backup_ref`) — linked from change rows, restorable via `hermes_cli/backup.py` quick-snapshot/restore
- [x] Irreversible-mint exception + test (`reversible=false`, `inverse_op=null`, explicit approval required, undo refused)
- [x] tests (unit + E2E + negative + irreversibility) green — `tests/hermes_cli/test_changes.py` + `test_changes_e2e.py` (temp `HERMES_HOME` + throwaway Postgres); `hermes changes` CLI registered in `main.py`
- [ ] System test on the system-test ECS passed (see *System testing* section) — **pending owner (Leo) coordination; not accessible from this PR**

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |
| 2026-07-11 | 2 | devin:8cec0d47 | Added System testing (system-test box) section as a per-FG DoD step | Leo: new 4/16 ECS = system-test host (+ prod for now), run after each FG's development |
| 2026-07-11 | 3 | devin:587066e0 | Implemented C5 change log + C6 consent policy + undo/redo + backup linkage + `hermes changes` CLI; added unit/E2E/negative/irreversibility tests | Wave-1 FG-12 build; publishes C5/C6 for Wave-2 (FG-07/08/10) |

## Cloud-agent prompt
> **[Wave 1 — start after Wave 0 merges]** Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-12`). Build the **change-management module**. Publish contract **C5** = append-only `changes(id, ts, actor_user_id, mode, target_kind∈{data,config,code}, op, inverse_op|null, reversible, approval_ref, backup_ref, payload)` and make every mutating capability emit one. Implement **undo/redo**: code/files via `tools/checkpoint_manager.py` `restore` (REUSE it), config via inverse-op replay, data via reversible inverse-ops + a redo stack. Publish contract **C6** = one approval/consent policy wrapping `tools/approval.py`+`tools/write_approval.py` with quiet-hours/rate-limit/consent (shared with FG-10 and used by 4.1/6.1). Wire **backup** (`hermes_cli/backup.py`) as `backup_ref`. Honour the documented exception: an **ERC-721 mint is `reversible=false`** and undo must refuse it (D6). Scope changes by contract C2 (owner sees/undoes all). Follow `AGENTS.md` (cache-sacred, footprint ladder, extend not duplicate). Add unit + E2E + negative-access + **irreversibility** tests (temp `HERMES_HOME` + throwaway Postgres); run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist (in this doc) passes** — coordinate that deploy/run with Leo.
