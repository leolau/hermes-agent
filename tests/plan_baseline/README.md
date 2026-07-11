# Baseline regression suite (master-plan)

These tests pin the **invariants of the primitives** that the 13 feature groups
(see `docs/design/master-plan/`) extend. If any FG regresses a reuse anchor,
one of these fails immediately — that is the point.

They follow the repo doctrine (`AGENTS.md`): **behavior/invariant contracts, not
change-detector tests.** They assert *how data must relate* (determinism,
isolation, status vocabulary, round-trip fidelity, cache-safe injection rules),
never a frozen current value (counts, model lists, config literals) that would
break on legitimate change.

| File | Locks | Guards FG |
|------|-------|-----------|
| `test_session_key_baseline.py` | `build_session_key` determinism + per-chat/per-user isolation + namespace shape | FG-03 (must keep single-account keys byte-stable while adding `account_id` — contract C4) |
| `test_goal_state_baseline.py` | `GoalState`/`GoalContract` JSON round-trip, defaults, back-compat load of legacy rows | FG-04 (registry sits above the loop without breaking serialization) |
| `test_todo_store_baseline.py` | Todo status vocabulary, replace/merge semantics, active-only post-compression injection | FG-06 (task tracking extends `TodoStore`; no 4th store, no status drift) |

Run just this suite:

```bash
scripts/run_tests.sh tests/plan_baseline/
```

**Definition of Done for every FG** = new FG tests green **AND** this baseline
suite green **AND** `ruff`/`ty` clean.
