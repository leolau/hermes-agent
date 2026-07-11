"""Unit invariants for FG-04 measurable goals: metric maths, priority
ordering, turn-budget scheduling, and cadence parsing.

These assert *behaviour contracts* (how values must relate), not frozen
snapshots — no enum counts or literal weight values are pinned.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hermes_cli.goals import (
    GOAL_PRIORITIES,
    GoalMetric,
    metrics_all_achieved,
    normalize_priority,
    priority_rank,
    priority_weight,
    render_metrics_block,
    verdict_for_metrics,
)
from hermes_cli.goal_registry import (
    GoalRecord,
    order_goals,
    parse_cadence,
    schedule_turn_budget,
)


def _goal(gid: str, priority: str, *, created=None, deadline=None) -> GoalRecord:
    return GoalRecord(
        id=gid,
        owner_user_id="o",
        visibility="shared",
        title=gid,
        description="",
        priority=priority,
        status="active",
        created_at=created,
        updated_at=created,
        deadline=deadline,
    )


# --- metric maths -----------------------------------------------------------


def test_at_least_metric_progress_and_achievement():
    m = GoalMetric("coverage", target=100, current=40, unit="%")
    assert not m.achieved
    assert m.progress_fraction == pytest.approx(0.4)
    m.current = 100
    assert m.achieved
    assert m.progress_fraction == 1.0
    m.current = 150  # overshoot never exceeds 1.0
    assert m.achieved
    assert m.progress_fraction == 1.0


def test_at_most_metric_is_achieved_when_under_target():
    m = GoalMetric("open_bugs", target=5, current=12, direction="at_most")
    assert not m.achieved
    # Closer to the ceiling means a higher fraction, monotonic toward done.
    assert 0.0 < m.progress_fraction < 1.0
    m.current = 5
    assert m.achieved and m.progress_fraction == 1.0
    m.current = 0
    assert m.achieved and m.progress_fraction == 1.0


def test_unmeasured_metric_is_never_achieved():
    m = GoalMetric("velocity")  # no target
    assert not m.is_measurable()
    assert not m.achieved
    assert m.progress_fraction == 0.0


def test_bad_direction_falls_back_to_at_least():
    m = GoalMetric("x", target=1, current=1, direction="sideways")
    assert m.direction == "at_least"
    assert m.achieved


def test_metric_dict_roundtrip_preserves_none_target():
    m = GoalMetric("m", target=None, current=3, unit="pts", cadence="1d")
    restored = GoalMetric.from_dict(m.to_dict())
    assert restored.target is None
    assert restored.current == 3
    assert restored.unit == "pts"
    assert restored.cadence == "1d"


# --- verdict + render -------------------------------------------------------


def test_verdict_requires_all_metrics_measured_and_achieved():
    assert verdict_for_metrics([])[0] == "continue"
    assert verdict_for_metrics([GoalMetric("a")])[0] == "continue"  # unmeasured
    assert verdict_for_metrics([GoalMetric("a", target=10, current=3)])[0] == "continue"
    done, reason = verdict_for_metrics([GoalMetric("a", target=10, current=10)])
    assert done == "done"
    assert "achieved" in reason
    assert not metrics_all_achieved([GoalMetric("a")])
    assert metrics_all_achieved([GoalMetric("a", target=1, current=1)])


def test_render_block_marks_measured_and_unmeasured():
    block = render_metrics_block(
        [GoalMetric("cov", target=100, current=100, unit="%"), GoalMetric("todo")]
    )
    assert "ACHIEVED" in block
    assert "unmeasured" in block
    assert render_metrics_block([]) == ""


# --- priority ordering ------------------------------------------------------


def test_priority_rank_orders_known_bands_and_sinks_unknowns():
    ranks = [priority_rank(p) for p in GOAL_PRIORITIES]
    assert ranks == sorted(ranks)  # tuple is declared most→least urgent
    assert priority_rank("nonsense") > priority_rank(GOAL_PRIORITIES[-1])


def test_priority_weight_is_monotonic_with_rank():
    for higher, lower in zip(GOAL_PRIORITIES, GOAL_PRIORITIES[1:]):
        assert priority_weight(higher) > priority_weight(lower)


def test_normalize_priority_defaults_unknown():
    assert normalize_priority(None)
    assert normalize_priority("HIGH") == "high"
    assert normalize_priority("bogus") == normalize_priority(None)


def test_order_goals_breaks_ties_by_deadline_then_age():
    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    a = _goal("a", "high", created=now)
    b = _goal("b", "high", created=now, deadline=now + timedelta(days=1))
    c = _goal("c", "critical", created=now)
    ordered = order_goals([a, b, c])
    assert ordered[0].id == "c"  # highest band first
    assert ordered[1].id == "b"  # deadline beats no-deadline within a band


# --- turn-budget scheduling -------------------------------------------------


def test_schedule_respects_priority_and_conserves_budget():
    goals = [_goal("hi", "critical"), _goal("mid", "medium"), _goal("lo", "low")]
    alloc = schedule_turn_budget(goals, 10)
    assert sum(alloc.values()) == 10  # budget conserved exactly
    assert alloc["hi"] >= alloc["mid"] >= alloc["lo"]  # priority invariant
    assert set(alloc) == {"hi", "mid", "lo"}  # every goal represented


def test_schedule_zero_budget_or_no_goals():
    assert schedule_turn_budget([], 10) == {}
    goals = [_goal("a", "high")]
    assert schedule_turn_budget(goals, 0) == {"a": 0}


# --- cadence parsing --------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1d", timedelta(days=1)),
        ("12h", timedelta(hours=12)),
        ("30m", timedelta(minutes=30)),
        ("2w", timedelta(weeks=2)),
        ("", None),
        ("bogus", None),
        ("5x", None),
    ],
)
def test_parse_cadence(text, expected):
    assert parse_cadence(text) == expected
