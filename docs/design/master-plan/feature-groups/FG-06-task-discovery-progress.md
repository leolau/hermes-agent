# FG-06 — Task discovery & progress tracking

**Wave:** 1 · **Owner agent:** _unassigned_ · **Status:** Not started

## Summary
Track **explicit tasks** (given by a user/agent) AND **autonomously discover
tasks** from conversations/feedback. After a number of **repeated prompts** for
the same thing, the agent **self-discovers a task** — inferring `title`,
`description`, **trigger state**, **completion state**, and **intermediate
progress states** (6.1) — and proposes it (approval-gated) rather than silently
acting. Tasks are visibility-scoped.

## Decisions applied
- D1/C2 (per-user vs shared tasks), C6 (propose discovered tasks without spamming), D2 (repetition signal from live store).

## Reuse map
- `tools/todo_tool.py` `TodoStore` — status vocabulary (`pending`/`in_progress`/`completed`/`cancelled`), merge semantics, prompt-injection of only active todos. **Base for explicit task tracking**; locked by `tests/plan_baseline/test_todo_store_baseline.py`.
- `tools/kanban_tools.py` — multi-board/stateful workflow for richer tasks with intermediate states.
- `hermes_cli/projects_db.py` — project/folder grouping.
- FG-05 live store — repetition/feedback signal source.

## Design / approach
1. **Explicit tasks:** reuse `TodoStore`/kanban; add `owner_user_id` +
   `visibility`; do NOT add a 4th store.
2. **Task discovery engine:** a monitor counts **repeated user prompts / recurring
   intents** (keyed on normalized intent, from the live store). When a threshold
   is crossed, it **synthesizes a task spec**:
   `{title, description, trigger_state, completion_state, progress_states[]}` and
   **proposes** it to the owner/user via C6 (approval, quiet-hours, rate-limit,
   cache-safe appended message). No autonomous execution of a discovered task
   without approval.
3. **Progress model:** tasks carry an ordered `progress_states[]`; current state
   + transitions are recorded (kanban movement is the reference UX).
4. **Anti-loop guard:** discovered tasks can't themselves generate new discovery
   signals (prevents self-amplifying task spam).

## Data model (Supabase `app_*` for discovered/coordination; SQLite todo core stays)
- `tasks(id, owner_user_id, visibility, title, description, trigger_state, completion_state, current_state, origin ∈ {explicit, discovered}, created_at)`.
- `task_progress_states(id, task_id FK, ordinal, name)` + `task_transitions(id, task_id FK, from_state, to_state, ts, actor)`.
- `intent_signals(id, normalized_intent, count, first_seen, last_seen, user_id)` — repetition counter for discovery.

## Dev/Prod + Supabase
Discovery/coordination in `app_*` via C3. Explicit todos keep the SQLite core
path (back-compat).

## Testing requirements
- Baseline: `test_todo_store_baseline.py` stays green.
- Unit: repetition threshold → task spec synthesis; progress-state transitions; anti-loop guard.
- Negative access: private task not visible cross-user; owner sees.
- E2E: repeat an intent N times → discovery proposes a task (via C6) → approve → task tracked with progress states.

## Dependencies
- **Blocked by:** FG-13 (C3), FG-01 (C2), FG-05 (signal store), FG-03 (conversation source), C6.
- **Blocks:** FG-09.

## Definition of Done
Tests green + baseline green + `ruff`/`ty` clean; no 4th task store introduced; discovery is proposal-only (approval-gated) and non-spammy (C6) with anti-loop guard.

## Progress checklist
- [ ] Scope explicit tasks (reuse TodoStore/kanban; add owner/visibility)
- [ ] Repetition/intent signal counter (live store)
- [ ] Discovery engine → task spec (trigger/completion/progress states)
- [ ] Proposal via C6 (approval, quiet-hours, cache-safe)
- [ ] Anti-loop guard
- [ ] tests (baseline + unit + negative + E2E) green

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |

## Cloud-agent prompt
> **[Wave 1 — start after Wave 0 + FG-03 C4 merge]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-06`). Implement **task tracking + autonomous task discovery**. For explicit tasks, REUSE `tools/todo_tool.py`/`tools/kanban_tools.py` (do NOT add a 4th store) and add `owner_user_id`+`visibility` (contract C2); `tests/plan_baseline/test_todo_store_baseline.py` must stay green. Add a **discovery engine** that counts repeated user prompts/recurring intents (from the FG-05 live store) and, past a threshold, **synthesizes a task spec** `{title, description, trigger_state, completion_state, progress_states[]}` and **proposes** it via the shared approval/quiet-hours/rate-limit policy (contract C6) using cache-safe appended messages — never auto-execute a discovered task, never mutate the system prompt. Include an **anti-loop guard** (discovered tasks don't feed discovery). Follow `AGENTS.md`. Add baseline + unit + negative-access + E2E tests (temp `HERMES_HOME` + throwaway Postgres); run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc.
