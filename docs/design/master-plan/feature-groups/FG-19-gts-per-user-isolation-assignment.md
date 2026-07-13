# FG-19 — Per-user GTS isolation + cross-user assignment

**Wave:** C (Phase-2) · **Owner agent:** _unassigned_ · **Status:** Not started

## Summary
Each user has their **own Goals/Tasks/Skills, independent** of others; the
**owner can view/access every user's GTS Centre**; and users can **assign
sub-goals and tasks to other users** (Req 19.0). Assignment introduces a
**per-item grant** on top of the existing shared/private model (C2): an item
stays owned+private to its creator but the **single assignee** (plus optional
**watchers**) gets scoped access to that specific item — so delegated work is
visible to the assignee **without** leaking the owner's other private data.
Ownership vs assignee semantics, permissions, notifications, and a full
assignment audit trail are defined here.

## Decisions applied
- **D15 — GTS is per-user isolated; owner sees all; cross-user assignment is a per-item grant (single assignee + optional watchers), not a visibility downgrade of the owner's other data.**
- Extends C2 (adds per-row grants beyond shared/private) + C9 (FG-18 GTS graph). C5 (assignment/reassignment/acceptance/completion audited), C6 (assignment notification respects consent/quiet-hours; cross-user side effects gated), C8 (assignment events traced).

## Reuse map
- FG-18 C9 graph (`goals`/`tasks` + hierarchy + score) — assignment fields attach here.
- FG-01 C2 `can_read`/`scope_filter` + Postgres RLS — **extended** with an "assigned/granted to me" clause (not a new access system).
- FG-10 (C6 human-comms) — assignment notifications + acceptance prompts ride the existing approval/quiet-hours surface.
- FG-12 C5 change log + FG-16 C8 trace — assignment lifecycle records.

## Design / approach
1. **Per-user namespace + owner access.** Every GTS item is `private:<owner>`
   by default; the **owner role bypasses** the filter (sees all users' GTS) —
   already how C2 works; this FG makes the GTS Centre respect it and adds an
   owner cross-user browse view (rendered by FG-17).
2. **Assignment = per-item grant.**
   - `assignee_user_id` on a **sub-goal or task** (top-level goals are **not**
     assignable — only the user manages their own top-level goals, per FG-18).
   - **Single assignee** (Leo's choice) + optional **watchers** (read-only) via
     an `item_grants(item_kind, item_id, user_id, grant ∈ {assignee, watcher})`
     table.
   - Access rule (extends C2): a user may read/act on a GTS item if it is
     **shared** OR **theirs** OR **granted to them** (assignee/watcher) OR they
     are **owner**. RLS updated to include the grant clause.
3. **Ownership vs assignee permissions.**
   - **Creator/owner-of-item:** full control (edit content, priority, due,
     evaluation method, delete, reassign).
   - **Assignee:** may **advance progress/status** and add progress notes on the
     assigned item and create **sub-tasks under it** (agent-created ones ride
     C6); may **not** change the item's **evaluation method** (user-owned, per
     FG-18) or reassign/delete it, and cannot see the owner's *other* private
     items.
   - **Watcher:** read-only on the granted item.
4. **Acceptance workflow.** Assignment sends a C6 notification; default is
   **auto-accepted with a decline option** (assignee can decline → grant
   revoked, audited). (Config can require explicit accept.)
5. **Agent-initiated assignment.** The agent may assign to another user **only
   under C6 approval** (cross-user side effect); the authorizing user/owner is
   recorded as actor.
6. **Full audit.** assign / reassign / accept / decline / progress / score-change
   each emit C5 + C8, attributing actor + subject user.
7. **Score ownership.** Score stays **auto-computed** (FG-18); the assignee's
   progress updates feed the computation but never hand-set the score; rollup to
   the owner's parent goal still holds.

## Data model (extends FG-18 `app_*`)
- `goals`/`tasks` `+ assignee_user_id|null` (tasks + **sub**-goals only).
- `item_grants(id, item_kind ∈ {goal,task}, item_id, user_id, grant ∈ {assignee,watcher}, granted_by, status ∈ {pending,accepted,declined,revoked}, ts)`.
- `assignment_events(id, item_kind, item_id, from_user, to_user, action, actor_user_id, ts)` — or fold into C5/C8 (prefer reuse).
- RLS policies updated: read/act if shared ∨ owner-of-row ∨ grant-exists ∨ owner-role.

## Dev/Prod + Supabase
Grants/assignments in `app_*` via C3; RLS enforced in Postgres (not just app
layer). Owner-role bypass mirrored in dev/prod schemas.

## Testing requirements
- Unit: grant CRUD; single-assignee invariant (a second assignee replaces/refuses per policy); watcher read-only; accept/decline/revoke transitions.
- **Negative access (required):** assignee sees **only** the assigned item, **not** the owner's other private GTS; a non-granted user sees nothing; owner sees all — enforced at **Postgres RLS**, not just app layer.
- Authority: assignee **cannot** change evaluation method / reassign / delete; **can** advance progress + add sub-tasks (agent sub-tasks ride C6). Top-level goals are **not** assignable.
- Agent-initiated assignment requires C6 approval.
- Audit: assign/reassign/accept/decline/progress emit C5 + C8 with correct actor/subject.
- E2E: user A assigns a sub-task to user B → B is notified (C6), accepts, advances progress → A's parent-goal score rolls up → A's other private items remain invisible to B → owner sees the whole chain.

## System testing (system-test box)
**Required after this FG's development completes** (part of DoD), on the new ECS (`hermes-systest`, 4/16, EIP `47.83.199.25`) against **staging** (`app_dev`) (**never prod**). See README §7.1. Checklist:
- With ≥2 real test users on the box: A assigns a sub-task to B; B is notified via the live channel (C6), accepts, and advances progress; A's parent score rolls up; **B cannot see A's other private GTS** (verified against real RLS); owner sees everything.
- Confirm top-level goals are not assignable and the assignee cannot alter the evaluation method (both refused + audited).
- Confirm assign/accept/progress events appear in the change log (C5) + trace (C8).
- **Gate:** not complete/promotable until this checklist passes.

## Dependencies
- **Blocked by:** FG-18 (C9 GTS graph), FG-01 (C2/roles), FG-10 (C6 notifications), FG-12 (C5), FG-16 (C8), FG-13 (C3).
- **Blocks:** rendered by FG-17 (assignment UI + owner cross-user browse).
- Extends contract **C2** (per-item grants) + **C9** (assignment fields).

## Definition of Done
Tests green (incl. negative-access RLS + authority + audit) + baseline green + `ruff`/`ty` clean; per-user isolation + owner-sees-all; single-assignee (+ watchers) per-item grants that don't leak owner's other private data; assignee permission boundary enforced; assignment lifecycle audited (C5/C8); score stays auto-computed; **ECS system test green**.

## Progress checklist
- [x] Per-user GTS namespace + owner cross-user access (C2 owner-bypass in GTS Centre; `list_goals_for_user` owner browse)
- [x] Per-item grants: `assignee_user_id` (tasks + sub-goals only) + `item_grants` (single assignee + watchers)
- [x] Extended `can_read`/`scope_filter` + Postgres RLS grant clause
- [x] Assignee permission boundary (progress/sub-tasks yes; eval-method/reassign/delete no) + top-level not assignable
- [x] Acceptance workflow + agent-initiated assignment via C6 + full C5/C8 audit
- [x] tests (unit + negative-access RLS + authority + audit + E2E) green
- [ ] System test on the system-test ECS passed (see *System testing* section) — **owner-gated** (needs ≥2 real users + live channel; deferred to Leo, not promotable until it passes)

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-12 | 1 | devin:8cec0d47 (for Leo) | Created FG doc | Phase-2 req 19.0: per-user GTS isolation, owner sees all, single-assignee cross-user assignment via per-item grants |
| 2026-07-04 | 2 | devin:3c64bcf2 (for Leo) | Implementation + full test suite complete; fixed a grant-clause bug (app-layer `scope_filter` correlated the "granted-to-me" `EXISTS` on an unqualified `id`, which resolved to `item_grants.id` inside the sub-select and never matched — the 3 GTS call sites now pass a table-qualified `id_column`, mirroring the RLS clause). Added `tests/hermes_cli/test_fg19_assignment_e2e.py` (lifecycle + score rollup + per-item isolation via **real Postgres RLS** + authority boundary + C6 approval + C5/C8 audit). Ticked all code/test checklist items. | DoD: real-path E2E for the access-control/datastore change; behavior over snapshots; ECS system test remains the only open gate (owner-controlled). |

## Cloud-agent prompt
> **[Phase-2 Wave C — start after FG-18 (C9) + FG-01/FG-10/FG-12/FG-16/FG-13]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-19`). Add **per-user GTS isolation + cross-user assignment** on top of FG-18's C9 graph. Each GTS item defaults `private:<owner>`; the **owner role sees all** (reuse C2 bypass) + an owner cross-user browse view. Add **cross-user assignment** as a **per-item grant** (NOT a visibility downgrade): `assignee_user_id` on **tasks and sub-goals only** (top-level goals are NOT assignable) + an `item_grants(item_kind,item_id,user_id,grant∈{assignee,watcher},status,...)` table — **single assignee** + optional read-only **watchers**. **Extend** C2's `can_read`/`scope_filter` + **Postgres RLS** with an "assigned/granted to me" clause (do NOT build a new access system) so an assignee sees ONLY the assigned item, never the owner's other private GTS. Assignee may advance progress/status + add sub-tasks (agent sub-tasks ride C6) but may NOT change the **evaluation method** (user-owned per FG-18), reassign, or delete. Assignment sends a C6 notification (default auto-accept + decline); **agent-initiated assignment requires C6 approval**. Score stays **auto-computed** (FG-18). Audit assign/reassign/accept/decline/progress via C5 + C8. Follow `AGENTS.md` (extend-don't-duplicate). Keep baseline green; add unit + **negative-access RLS** + authority + audit + real-Postgres E2E tests (temp `HERMES_HOME` + throwaway Postgres); run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist passes** — coordinate with Leo.
