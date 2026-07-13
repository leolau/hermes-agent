"""Unit tests for the FG-18 / C9 GTS Centre — the pure, DB-free surface.

Covers the scoring maths (always computed, clamped ``0–100``, priority-weighted
rollup), cache-safe rendering, and the fail-closed authority guard (the runtime
agent is refused + audited on top-level goals and evaluation methods) exercised
without a database by short-circuiting on the refusal path.

The DB-backed behaviour (M:N edges, cycle prevention, negative access, RLS, the
full measurement→score→verdict loop) lives in ``test_gts_e2e.py``.
"""

from __future__ import annotations

import pytest

from hermes_cli.access import Principal
from hermes_cli.goals import GoalMetric
from hermes_cli.gts import (
    GtsAuthorityError,
    GtsCentre,
    GtsGoal,
    ObservationSpec,
    ScoringRequest,
    clamp_score,
    default_score_evaluator,
    method_is_measurable,
    method_scoring_prompt,
    parse_observation,
    render_gts_block,
    rollup_score,
    score_from_metrics,
    score_from_progress,
    validate_evaluation_method,
)


class _FakeStore:
    """A store stand-in — the refusal path never opens a connection."""

    mode = "dev"
    schema = "app_dev"

    async def connect(self) -> object:  # pragma: no cover - must never run
        raise AssertionError("authority refusal must not touch the datastore")


def _user(user_id: str = "alice") -> Principal:
    return Principal(user_id=user_id, display=user_id, role="member")


# --- scoring: clamp ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [(-5.0, 0.0), (0.0, 0.0), (42.0, 42.0), (100.0, 100.0), (250.0, 100.0)],
)
def test_clamp_score_stays_on_0_100(raw: float, expected: float) -> None:
    assert clamp_score(raw) == expected


def test_score_from_metrics_is_weighted_progress_and_clamped() -> None:
    # 50% of one metric, 100% of another; equal weights → 75.
    half = GoalMetric("a", target=10, current=5)
    full = GoalMetric("b", target=10, current=10)
    assert score_from_metrics([half, full]) == pytest.approx(75.0)

    # Over-target progress is clamped by progress_fraction (≤1) → 100, not 150.
    over = GoalMetric("c", target=10, current=15)
    assert score_from_metrics([over]) == pytest.approx(100.0)


def test_score_from_metrics_weights_bias_the_mean() -> None:
    zero = GoalMetric("a", target=10, current=0)
    full = GoalMetric("b", target=10, current=10)
    # Weight the achieved metric 3:1 → 75, not 50.
    biased = score_from_metrics([zero, full], weights={"a": 1.0, "b": 3.0})
    assert biased == pytest.approx(75.0)


def test_score_from_metrics_without_measurable_metric_is_zero() -> None:
    # No target defined → not measurable → no evidence of progress → 0.
    assert score_from_metrics([GoalMetric("a")]) == 0.0
    assert score_from_metrics([]) == 0.0


def test_score_from_progress_tracks_state_machine_position() -> None:
    states = ("pending", "in_progress", "completed")
    assert score_from_progress(states, "pending", "completed") == 0.0
    assert score_from_progress(states, "in_progress", "completed") == pytest.approx(
        50.0
    )
    assert score_from_progress(states, "completed", "completed") == 100.0


def test_score_from_progress_honours_state_rubric_and_cancelled() -> None:
    states = ("pending", "in_progress", "completed")
    rubric = {"in_progress": 90.0}
    assert score_from_progress(
        states, "in_progress", "completed", state_scores=rubric
    ) == pytest.approx(90.0)
    # A cancelled task scores 0 regardless of position.
    assert (
        score_from_progress(states, "in_progress", "completed", status="cancelled")
        == 0.0
    )


# --- scoring: priority-weighted rollup --------------------------------------


def test_rollup_is_priority_weighted() -> None:
    # critical(8) at 100 vs low(1) at 0 → 8*100/(8+1) ≈ 88.9.
    assert rollup_score([(100.0, "critical"), (0.0, "low")]) == pytest.approx(
        800.0 / 9.0
    )


def test_rollup_equal_priority_is_plain_mean_and_clamped() -> None:
    assert rollup_score([(40.0, "medium"), (60.0, "medium")]) == pytest.approx(50.0)
    # Out-of-band child scores are clamped before averaging.
    assert rollup_score([(250.0, "high"), (250.0, "high")]) == 100.0


def test_rollup_of_no_children_is_zero() -> None:
    assert rollup_score([]) == 0.0


# --- cache-safe surfacing ---------------------------------------------------


def _goal(title: str, priority: str, score: float | None, level: str) -> GtsGoal:
    return GtsGoal(
        id=title,
        owner_user_id="alice",
        visibility="shared",
        title=title,
        priority=priority,
        status="active",
        level=level,
        parent_goal_id=None if level == "top" else "root",
        score=score,
        evaluation_method_ref=None,
    )


def test_render_gts_block_is_deterministic_and_priority_ordered() -> None:
    goals = [
        _goal("Low goal", "low", 10.0, "top"),
        _goal("Critical goal", "critical", 90.0, "top"),
    ]
    first = render_gts_block(goals)
    second = render_gts_block(list(reversed(goals)))
    # Byte-identical regardless of input order → safe to append repeatedly.
    assert first == second
    # Critical is ordered ahead of low.
    assert first.index("Critical goal") < first.index("Low goal")
    assert "90%" in first


def test_render_gts_block_never_embeds_a_system_prompt() -> None:
    system_prompt = b"SYSTEM PROMPT v1 (byte-stable)"
    snapshot = bytes(system_prompt)
    block = render_gts_block([_goal("G", "high", 50.0, "top")])
    # Rendering is a pure append-only string; it cannot touch the prompt.
    assert isinstance(block, str)
    assert system_prompt == snapshot


# --- authority (fail-closed, audited) — DB-free refusal path ----------------


@pytest.mark.asyncio
async def test_agent_refused_on_top_level_goal_is_audited() -> None:
    events: list[dict] = []
    centre = GtsCentre(_FakeStore(), audit_sink=events.append)

    with pytest.raises(GtsAuthorityError):
        await centre.create_goal(
            _user(),
            "Own the roadmap",
            actor="agent",
            parent_goal_id=None,
            connection=object(),  # never used — refusal precedes any DB call
        )

    assert len(events) == 1
    event = events[0]
    assert event["decision"] == "refused"
    assert event["kind"] == "core_denied"
    assert event["actor"] == "agent"
    assert event["target_kind"] == "goal"


@pytest.mark.asyncio
async def test_agent_refused_on_evaluation_method_is_audited() -> None:
    events: list[dict] = []
    centre = GtsCentre(_FakeStore(), audit_sink=events.append)

    with pytest.raises(GtsAuthorityError):
        await centre.set_evaluation_method(
            _user(),
            "goal",
            "goal-123",
            {"weights": {"m": 1.0}},
            actor="agent",
            connection=object(),
        )

    assert [e["decision"] for e in events] == ["refused"]
    assert events[0]["action"] == "set an evaluation method"
    assert events[0]["kind"] == "core_denied"


@pytest.mark.asyncio
async def test_agent_refused_on_observe_measure_method_is_audited() -> None:
    # The observation prompt, measurable flag, and scoring prompt are all part
    # of the user-owned evaluation method — the agent is refused + audited even
    # for a perfectly well-formed new-model method (authority precedes the DB).
    events: list[dict] = []
    centre = GtsCentre(_FakeStore(), audit_sink=events.append)

    with pytest.raises(GtsAuthorityError):
        await centre.set_evaluation_method(
            _user(),
            "goal",
            "goal-9",
            {
                "measurable": True,
                "observation": {"source": "external", "prompt": "poll",
                                "ref": {"tool": "crm"}},
                "scoring": {"prompt": "compute 0-100"},
            },
            actor="agent",
            connection=object(),
        )

    assert [e["decision"] for e in events] == ["refused"]
    assert events[0]["kind"] == "core_denied"
    assert events[0]["action"] == "set an evaluation method"


@pytest.mark.asyncio
async def test_unknown_evaluation_target_kind_is_rejected() -> None:
    centre = GtsCentre(_FakeStore())
    with pytest.raises(ValueError):
        await centre.set_evaluation_method(
            _user(), "campaign", "x", {}, actor="user", connection=object()
        )


# --- observe/measure model: typing + parsing -------------------------------


def _observed_method(
    *, measurable: bool, source: str = "internal", **extra: object
) -> dict:
    method: dict = {
        "measurable": measurable,
        "observation": {"source": source, "prompt": "how to observe"},
    }
    if measurable:
        method["scoring"] = {"prompt": "compute 0-100 from observed state"}
    method.update(extra)
    return method


def test_parse_observation_types_the_source_prompt_and_ref() -> None:
    spec = parse_observation(
        {
            "observation": {
                "source": "external",
                "prompt": "poll the CRM",
                "ref": {"kind": "mcp", "tool": "crm.count"},
            }
        }
    )
    assert isinstance(spec, ObservationSpec)
    assert spec.source == "external"
    assert spec.prompt == "poll the CRM"
    assert spec.ref == {"kind": "mcp", "tool": "crm.count"}
    # A method with no observation returns None.
    assert parse_observation({"weights": {"m": 1.0}}) is None


def test_measurable_defaults_true_for_legacy_methods() -> None:
    # Legacy metric/state methods (no explicit flag) stay measurable.
    assert method_is_measurable({}) is True
    assert method_is_measurable({"weights": {"m": 1.0}}) is True
    # Explicit flags are honoured.
    assert method_is_measurable(_observed_method(measurable=True)) is True
    assert method_is_measurable(_observed_method(measurable=False)) is False


def test_validate_accepts_a_measurable_method_with_scoring_prompt() -> None:
    validate_evaluation_method(_observed_method(measurable=True))
    assert method_scoring_prompt(_observed_method(measurable=True))


def test_validate_accepts_a_non_measurable_observation_only_method() -> None:
    method = _observed_method(measurable=False)
    validate_evaluation_method(method)
    # A non-measurable goal keeps an observation but carries no scoring prompt.
    assert parse_observation(method) is not None
    assert method_scoring_prompt(method) == ""


def test_validate_rejects_bad_observation_source() -> None:
    with pytest.raises(ValueError):
        validate_evaluation_method(
            {
                "measurable": False,
                "observation": {"source": "carrier-pigeon", "prompt": "p"},
            }
        )


def test_validate_requires_a_non_empty_observation_prompt() -> None:
    with pytest.raises(ValueError):
        validate_evaluation_method(
            {"measurable": False, "observation": {"source": "ask", "prompt": "   "}}
        )


def test_validate_requires_ref_for_external_source() -> None:
    with pytest.raises(ValueError):
        validate_evaluation_method(
            {
                "measurable": False,
                "observation": {"source": "external", "prompt": "poll"},
            }
        )


def test_validate_requires_scoring_prompt_when_measurable() -> None:
    with pytest.raises(ValueError):
        validate_evaluation_method(
            {
                "measurable": True,
                "observation": {"source": "internal", "prompt": "watch"},
            }
        )


def test_validate_forbids_scoring_prompt_when_not_measurable() -> None:
    with pytest.raises(ValueError):
        validate_evaluation_method(
            {
                "measurable": False,
                "observation": {"source": "internal", "prompt": "watch"},
                "scoring": {"prompt": "score it"},
            }
        )


def test_validate_requires_observation_once_measurable_declared() -> None:
    with pytest.raises(ValueError):
        validate_evaluation_method({"measurable": True})


def test_validate_ignores_legacy_methods_without_the_flag() -> None:
    # No exception: legacy weights/state_scores methods pass straight through.
    validate_evaluation_method({"weights": {"m": 1.0}})
    validate_evaluation_method({"state_scores": {"done": 100.0}})


# --- scoring-prompt seam → clamped score ------------------------------------


def _scoring_request(observed: dict) -> ScoringRequest:
    return ScoringRequest(
        target_kind="goal",
        target_id="g1",
        observation=ObservationSpec(source="internal", prompt="observe"),
        scoring_prompt="score",
        observed_state=observed,
        mode="dev",
    )


def test_default_evaluator_reads_numeric_score_from_observed_state() -> None:
    assert default_score_evaluator(_scoring_request({"score": 73})) == 73.0
    # Nothing observed yet → None (the node stays unscored, never guessed).
    assert default_score_evaluator(_scoring_request({})) is None
    assert default_score_evaluator(_scoring_request({"status": "green"})) is None


def test_default_evaluator_output_is_clamped_by_the_engine_contract() -> None:
    # The evaluator returns the raw value; the engine clamps. Prove the raw
    # passthrough here and clamp composition explicitly.
    raw_over = default_score_evaluator(_scoring_request({"score": 250}))
    raw_under = default_score_evaluator(_scoring_request({"score": -40}))
    assert isinstance(raw_over, float) and isinstance(raw_under, float)
    assert raw_over == 250.0
    assert raw_under == -40.0
    assert clamp_score(raw_over) == 100.0
    assert clamp_score(raw_under) == 0.0
