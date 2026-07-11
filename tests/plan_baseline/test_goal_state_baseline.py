"""Baseline invariants for goal serialization.

Locks the ``GoalState`` / ``GoalContract`` round-trip + back-compat behaviour
that FG-04 (prioritised, measurable goal registry) builds on. FG-04 adds a
registry ABOVE the per-session Ralph loop; it must not break how a goal row
serializes or how legacy rows load.
"""

import json

from hermes_cli.goals import DEFAULT_MAX_TURNS, GoalContract, GoalState


def test_goal_state_json_roundtrip_preserves_core_fields():
    gs = GoalState(goal="ship the plan", max_turns=5)
    gs.subgoals = ["a", "b"]
    restored = GoalState.from_json(gs.to_json())
    assert restored.goal == "ship the plan"
    assert restored.status == gs.status
    assert restored.max_turns == 5
    assert restored.subgoals == ["a", "b"]


def test_goal_state_default_max_turns():
    assert GoalState(goal="x").max_turns == DEFAULT_MAX_TURNS


def test_goal_contract_roundtrip():
    gs = GoalState(
        goal="g",
        contract=GoalContract(outcome="O", verification="V", stop_when="S"),
    )
    restored = GoalState.from_json(gs.to_json())
    assert restored.contract.outcome == "O"
    assert restored.contract.verification == "V"
    assert restored.contract.stop_when == "S"


def test_empty_contract_is_empty():
    assert GoalContract().is_empty()
    assert not GoalContract(outcome="done when X").is_empty()


def test_legacy_row_without_new_fields_loads():
    # A goal row persisted before contract/subgoals/waiting_* existed must still
    # load with safe defaults (no crash, no data loss on the fields that exist).
    legacy = json.dumps(
        {"goal": "legacy goal", "status": "active", "turns_used": 3, "max_turns": 10}
    )
    gs = GoalState.from_json(legacy)
    assert gs.goal == "legacy goal"
    assert gs.status == "active"
    assert gs.turns_used == 3
    assert gs.max_turns == 10
    assert gs.subgoals == []
    assert gs.contract.is_empty()
