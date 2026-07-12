# FG-18 — GTS Centre (Goals → Tasks → Skills)

**Wave:** B (Phase-2) · **Owner agent:** devin:b9d4f38f (for Leo) · **Status:** Implemented (code + tests green); ECS system-test box + prod promotion pending (gated, owned by Leo)

## Summary
A first-class dashboard surface — the **GTS Centre** — that helps a user
organize **Goals, Tasks, and Skills** cleanly and efficiently (Req 18.0). It is
a **Core tool (C7): its implementation and governing rules are NOT editable by
the user or the runtime agent** (only human devs via PR); users/agents manage
GTS *data* within its rules. It **unifies and extends** the existing FG-04
goal registry + FG-06 task infra + skills — **not a new parallel store**. Key
model: **tasks ↔ many goals** and **skills ↔ many tasks** (many-to-many); goals
and tasks are **hierarchical with priorities**; **only the user creates/manages
top-level goals**, while the **agent may create sub-goals, tasks, and
sub-tasks**; each goal/task has a **progress measure + a score (0–100%, auto-
calculated)** where **only the user sets/manages the evaluation method** and the
score is **never hand-set**.

## Decisions applied
- **D14 — GTS Centre is Core (C7): implementation + governing rules immutable to user/agent; only GTS *data* is mutable, within the Centre's authority rules.**
- Extends FG-04 (goals/priority/metrics/progress) + FG-06 (tasks/progress states) + skills — reuse, do not duplicate (no new goal/task store).
- C2 (per-user vs shared; owner sees all), C5 (every GTS change audited), C6 (agent-created sub-goals/tasks that have side effects are approval-gated), C8 (all GTS actions traced), prompt-cache sacred (GTS state surfaced via tool results / appended messages, **never** injected into the live system prompt).

## Reuse map
- `hermes_cli/goal_registry.py` (FG-04) — `goals`/`goal_metrics`/`goal_progress` + priority/scheduling. **Base for Goals**; add hierarchy + score normalization + authority levels.
- `hermes_cli/goals.py` `GoalContract`/`GoalMetric`/`verdict_for_metrics` — the computed-achievement engine; **base for evaluation methods + auto-score**.
- FG-06 `tasks`/`task_progress_states`/`task_transitions` (+ `tools/todo_tool.py`, `tools/kanban_tools.py`) — **base for Tasks**; add sub-task hierarchy.
- Existing **skills** (`skills/`, skill store) — registered as GTS **Skill** nodes; add skill↔task links (don't move skill content into the DB unnecessarily — reference it).
- FG-01 C2 scope helpers, FG-12 C5, FG-16 C8, FG-13 C3 store router.

## Design / approach
1. **Contract C9 — GTS graph (published here; assignment added in FG-19).**
   Unified nodes + typed edges over the existing stores:
   - **Goal** (extends FG-04 `goals`): `+ parent_goal_id|null`, `+ level`
     (`top` | `sub`), `+ score` (0–100, computed), keeps `priority`,
     `owner_user_id`, `visibility`.
   - **Task** (extends FG-06 `tasks`): `+ parent_task_id|null`, `+ priority`,
     `+ score` (0–100, computed).
   - **Skill**: a registry node referencing existing skill content.
   - Edges: `task_goals(task_id, goal_id)` **M:N**; `task_skills(task_id,
     skill_id)` **M:N**; goal/task self-hierarchy via `parent_*_id`.
2. **Authority model (level-based).**
   - **Top-level goals:** create/modify/delete = **user only**. The runtime
     agent is refused (guard + audit).
   - **Sub-goals, tasks, sub-tasks:** the agent **may** create/manage within a
     user-authorized parent; side-effecting ones ride C6.
   - **Evaluation method:** **user-only** (set/change). The agent can record
     measurements/progress but **cannot alter the method** — enforced as a
     Core-protected field (C7/C9), audited (C5).
3. **Progress + auto-score (0–100%).** Reuse FG-04's computed verdict: each
   goal/task has a user-defined **evaluation method** (`GoalMetric`-style:
   target/current/unit/source_query, or a weighted rubric). Score is **always
   computed**, clamped `0 ≤ score ≤ 100`, never hand-set. **Rollup:** a parent's
   score = priority-weighted aggregate of its children's scores (composable up
   the hierarchy).
4. **Hierarchy + priority integrity.** Cycle prevention on `parent_*_id`;
   priority ordering per level; states for incomplete/blocked/cancelled/archived
   preserved from FG-04/06.
5. **Cache-safe surfacing.** GTS state reaches the agent only via **tool
   results / appended continuation messages**, never by mutating the system
   prompt mid-conversation.
6. **Dashboard surface (rendered by FG-17).** A GTS Centre icon/link: goal/task
   tree with priorities + scores, skill associations, progress views.

## Data model (Supabase `app_*`, extending FG-04/06)
- `goals` (FG-04) `+ parent_goal_id`, `level`, `score`, `evaluation_method_ref`.
- `tasks` (FG-06) `+ parent_task_id`, `priority`, `score`, `evaluation_method_ref`.
- `skills_registry(id, owner_user_id, visibility, name, skill_ref, ...)`.
- `task_goals(task_id, goal_id)`, `task_skills(task_id, skill_id)` — M:N joins.
- `evaluation_methods(id, target_kind ∈ {goal,task}, target_id, method_json, set_by_user_id, locked:true)` — user-owned, agent-immutable.
- Reuse `goal_progress`/`task_progress_states`/`task_transitions` for history; C5 for change audit; C8 for traces.

## Dev/Prod + Supabase
GTS data in `app_*` via C3; the Ralph execution loop stays in SQLite core
(FG-04). Channels force prod. GTS Centre engine code is Core (C7).

## Testing requirements
- Baseline: `test_goal_state_baseline.py` + `test_todo_store_baseline.py` stay green (no break to FG-04/06 serialization).
- Unit: M:N link CRUD (task↔goals, skill↔tasks); hierarchy + **cycle prevention**; priority ordering; **score always computed + clamped 0–100**; **rollup** (priority-weighted child→parent).
- Authority: agent **cannot** create/modify a **top-level goal** (refused + audited); agent **can** create sub-goal/task under an authorized parent; agent **cannot** change an evaluation method (refused + audited); user can.
- Negative access: user A can't see user B's private goal/task/skill; owner can.
- E2E: user creates top-level goal + evaluation method → agent adds sub-goals/tasks/sub-tasks + links skills → progress recorded → **score auto-computed and rolls up** → judge/verdict sees the metric.
- Cache-safety: GTS updates never mutate the system prompt (prompt bytes unchanged).

## System testing (system-test box)
**Required after this FG's development completes** (part of DoD), on the new ECS (`hermes-systest`, 4/16, EIP `47.83.199.25`) against **staging** (`app_dev`) (**never prod**). See README §7.1. Checklist:
- On the box: user creates a top-level goal + evaluation method; the live agent adds sub-goals/tasks/sub-tasks and links skills (M:N) but is **refused** when it tries to create a top-level goal or change the evaluation method (both audited).
- Record progress → confirm **scores auto-compute, clamp to 100%, and roll up** the hierarchy by priority weighting.
- Confirm GTS updates are cache-safe on the live agent and scope-isolated via real RLS.
- **Gate:** not complete/promotable until this checklist passes.

## Dependencies
- **Blocked by:** FG-04 (goals), FG-06 (tasks), FG-01 (C2), FG-12 (C5/C6), FG-13 (C3), FG-14 (C7 marks engine + evaluation methods Core). Consumes FG-16 (C8).
- **Blocks:** FG-19 (per-user isolation + assignment builds on C9); rendered by FG-17.
- Publishes contract **C9** (assignment fields reserved for FG-19).

## Definition of Done
Tests green (incl. authority + cycle + score/rollup + cache-safety + negative access) + baseline green + `ruff`/`ty` clean; unifies FG-04/06/skills with M:N + hierarchy + priorities; top-level goals + evaluation methods are user-only (agent refused + audited); scores auto-computed/clamped/rolled-up; GTS engine is Core (C7); **ECS system test green**.

## Progress checklist
- [x] C9 unified graph over FG-04 goals + FG-06 tasks + skills (parent hierarchy; M:N `task_goals`/`task_skills`) — `hermes_cli/gts.py` (`GtsCentre`), extending `goal_registry`/`task_registry`; adds `skills_registry`, `task_goals`, `task_skills`, `evaluation_methods`.
- [x] Authority model (user-only top-level goals + evaluation methods; agent sub-goals/tasks; refusals audited) — fail-closed `GtsActor` guard; refusals emit C8 `core_denied` + a durable `<HERMES_HOME>/audit/gts_authority.jsonl` row + optional C5 sink.
- [x] Auto-score (0–100, clamped, computed) + priority-weighted rollup — `score_from_metrics`/`score_from_progress`/`rollup_score`; reuses `GoalMetric.progress_fraction`, `priority_weight`, `verdict_for_metrics`.
- [x] Cycle prevention + priority ordering + incomplete/blocked/cancelled/archived states — ancestor-walk cycle guard on reparent; priority-ordered listing; FG-04/06 states preserved.
- [x] Cache-safe surfacing (tool results/appended messages only) — `render_gts_block` (append-only, byte-stable); no API mutates the system prompt.
- [x] tests (baseline + unit + authority + negative + E2E + cache-safety) green — `tests/hermes_cli/test_gts.py` (unit) + `test_gts_e2e.py` (real-Postgres); baseline + goal/task/access/core-boundary suites green; `ruff`/`ty` clean on the new module.
- [ ] System test on the system-test ECS passed (see *System testing* section) — **separate gated step owned by Leo/requester; NOT run in this session.**

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-12 | 1 | devin:8cec0d47 (for Leo) | Created FG doc | Phase-2 req 18.0: GTS Centre as a Core tool unifying goals/tasks/skills (M:N, hierarchy, priorities, user-only top goals + eval methods, auto-score) |
| 2026-07-12 | 2 | devin:b9d4f38f (for Leo) | Implemented C9 GTS Centre (`hermes_cli/gts.py`) extending FG-04/06 + skills; added authority model, cycle-safe hierarchy, M:N edges, `skills_registry`/`evaluation_methods`, computed+clamped scores with priority-weighted rollup, cache-safe surfacing; marked the engine Core in `core_manifest.yaml` + `agent/core_boundary.py`; added unit + real-Postgres E2E tests | Deliver the GTS Centre per this spec's Design/Testing/DoD. ECS system-test box + prod promotion remain separate gated steps owned by Leo (not run here). |

## Cloud-agent prompt
> **[Phase-2 Wave B — start after FG-04/FG-06/FG-01/FG-12/FG-13 (Phase-1) merged + FG-14 C7]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-18`). Build the **GTS Centre** by **extending** FG-04 (`hermes_cli/goal_registry.py`, `goals.py`) + FG-06 (`tasks`/kanban/todo) + existing skills — **do NOT create a new goal/task store**. Publish contract **C9**: a unified graph with **hierarchical** goals + tasks (`parent_*_id`, cycle-safe), **priorities**, **M:N** `task_goals` and `task_skills`, and a `skills_registry` referencing existing skill content. Enforce the **authority model**: **only the user** creates/manages **top-level goals** and **evaluation methods** (agent refused + audited via C5/C8); the **agent may** create sub-goals/tasks/sub-tasks under an authorized parent (side-effecting ones ride C6). Each goal/task has a **user-defined evaluation method** and an **auto-computed score clamped 0–100** (never hand-set), with a **priority-weighted rollup** from children to parent — reuse FG-04's `GoalMetric`/`verdict_for_metrics`. The GTS engine + evaluation-method fields are **Core (C7)**. Surface GTS state to the agent **only via tool results / appended messages — never mutate the system prompt** (cache sacred). Route via C3; scope via C2 (owner sees all). Follow `AGENTS.md` (extend-don't-duplicate, footprint ladder). Keep `tests/plan_baseline/` green; add unit + authority + cycle + score/rollup + negative-access + cache-safety + real-Postgres E2E tests (temp `HERMES_HOME` + throwaway Postgres); run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist passes** — coordinate with Leo.
