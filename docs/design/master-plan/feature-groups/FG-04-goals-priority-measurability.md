# FG-04 — Goals with priority + measurability/progress

**Wave:** 1 · **Owner agent:** _unassigned_ · **Status:** Not started

## Summary
Extend the existing per-session goal loop into a **prioritised, multi-goal
registry** where each goal has **measurable success criteria** and **tracked
incremental progress**. Even when metrics aren't given up front, the
self-learning layer **monitors user feedback and proactively asks for the
missing measurement info at appropriate times** (4.1). Goals are
visibility-scoped (per-user / shared; owner sees all).

## Decisions applied
- D1/C2 (per-user vs shared goals), D2 (goal context from hybrid memory), C6 (proactive-ask quiet-hours/consent/rate-limit).

## Reuse map
- `hermes_cli/goals.py` — `GoalState` (status/turns/wait-barriers/subgoals) + **`GoalContract`** (`outcome`, `verification`, `constraints`, `boundaries`, `stop_when`) + the judge loop. **This is the base**; the registry sits *above* the Ralph loop (does not replace it).
- SessionDB `state_meta` (`goal:<session_id>`) — current persistence; app-level registry moves to Supabase `app_*` for cross-session/cross-user querying.
- `tools/approval.py` / consent policy (C6) — for proactive asks.

## Design / approach
1. **Goal registry (above the loop):** persistent, prioritised goals with
   `priority`, `status`, `owner_user_id`, `visibility`, and a **`GoalMetric`**
   set. The existing `/goal` Ralph loop remains the *execution* mechanism for an
   active goal.
2. **`GoalMetric`** (first-class, on/beside `GoalContract`):
   `{name, target, current, unit, source_query, cadence}` so "achieved?" and
   "incremental progress" are **computed**, not vibes. `source_query` reads the
   live store (FG-05) or a tool.
3. **Proactive measurement solicitation (4.1):** a monitor watches feedback;
   when a goal lacks a measurable metric (or progress is stale), it **asks the
   user** — but only through the shared consent/quiet-hours/rate-limit policy
   (C6), and via appended messages (cache-safe). Answers update the metric.
4. **Priority scheduling:** when multiple goals are active, higher-priority
   goals get turn budget first; ties broken by staleness/deadline.

## Data model (Supabase `app_*`)
- `goals(id, owner_user_id, visibility, title, description, priority, status, created_at, deadline?)`.
- `goal_metrics(id, goal_id FK, name, target, current, unit, source_query, cadence, last_measured_at)`.
- `goal_progress(id, goal_id FK, ts, value, note)` — incremental history.

## Dev/Prod + Supabase
Registry in `app_*` via C3. The per-session Ralph loop stays in SessionDB
(core), unchanged.

## Testing requirements
- Baseline: `test_goal_state_baseline.py` (GoalState/GoalContract roundtrip + back-compat) stays green — FG-04 must not break existing goal serialization.
- Unit: metric evaluation (target vs current → achieved), priority ordering, progress append.
- Negative access: user A can't see user B's private goal; owner can.
- E2E: create goal w/o metric → monitor asks (respecting quiet-hours) → answer sets metric → progress recorded → judge sees metric in verdict.

## Dependencies
- **Blocked by:** FG-13 (C3), FG-01 (C2), FG-05 (context/metric source), C6 (from FG-10/12).
- **Blocks:** FG-09 (goal management).

## Definition of Done
Tests green + baseline green + `ruff`/`ty` clean; registry sits above the Ralph loop without breaking it; metrics computed; proactive asks ride C6.

## Progress checklist
- [ ] Goal registry (priority/status/scope) above the Ralph loop
- [ ] `GoalMetric` + computed achievement + progress history
- [ ] Proactive measurement solicitation via C6 (cache-safe asks)
- [ ] Priority scheduling
- [ ] tests (baseline + unit + negative + E2E) green

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |

## Cloud-agent prompt
> **[Wave 1 — start after Wave 0 merges]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-04`). Build a **prioritised, measurable multi-goal registry ABOVE** the existing per-session Ralph loop in `hermes_cli/goals.py` — reuse `GoalState`/`GoalContract`/judge, DO NOT replace or break them (`tests/plan_baseline/test_goal_state_baseline.py` must stay green). Add a Supabase `app_*` registry (`goals`, `goal_metrics`, `goal_progress`) with `priority`, visibility scoping (contract C2, owner sees all), and a first-class **`GoalMetric{name,target,current,unit,source_query,cadence}`** so achievement + incremental progress are **computed**. Add a **proactive measurement monitor** (4.1) that watches feedback and, when metrics are missing/stale, **asks the user via appended messages through the shared consent/quiet-hours/rate-limit policy (contract C6)** — never mutate the system prompt. Add priority scheduling for turn budget. Follow `AGENTS.md` (cache-sacred, footprint ladder). Add baseline + unit + negative-access + E2E tests (temp `HERMES_HOME` + throwaway Postgres); run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc.
