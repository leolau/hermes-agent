"""Unit invariants for the FG-04 proactive measurement monitor and its
contract-C6 gate (consent / quiet-hours / rate-limit).

The monitor is exercised against a tiny in-memory fake store so these stay
fast and DB-free; the real datastore path is covered by the E2E test. The
key invariant asserted here is that the monitor **never** bypasses the C6
policy and delivers asks only via the injected (cache-safe) ``ask_fn``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from hermes_cli.access import Principal
from hermes_cli.goal_monitor import (
    ProactiveAskConfig,
    ProactiveAskPolicy,
    ProactiveMeasurementMonitor,
    build_question,
)
from hermes_cli.goal_registry import GoalRecord, MeasurementGap

NOON = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def _principal(user_id="alice", role="member") -> Principal:
    return Principal(user_id=user_id, display=user_id, role=role)


def _gap(reason: str, metric_name=None) -> MeasurementGap:
    goal = GoalRecord(
        id=f"goal-{reason}",
        owner_user_id="alice",
        visibility="shared",
        title="Ship v2",
        description="",
        priority="high",
        status="active",
        created_at=NOON,
        updated_at=NOON,
        deadline=None,
    )
    return MeasurementGap(goal, reason, metric_name)


class FakeStore:
    """Minimal async surface the monitor depends on."""

    def __init__(self, gaps: List[MeasurementGap], last_ask: Optional[datetime] = None):
        self._gaps = gaps
        self._last_ask = last_ask
        self.logged: List[tuple] = []

    async def measurement_gaps(self, principal, *, now=None, connection=None):
        return list(self._gaps)

    async def last_ask_at(self, user_id, *, connection=None):
        return self._last_ask

    async def log_ask(self, principal, goal_id, question, *, metric_name=None, connection=None):
        self.logged.append((goal_id, question, metric_name))


# --- policy: quiet hours ----------------------------------------------------


def test_quiet_hours_wraparound_blocks_overnight():
    policy = ProactiveAskPolicy(ProactiveAskConfig(quiet_start_hour=22, quiet_end_hour=8))
    night = NOON.replace(hour=2)
    day = NOON.replace(hour=12)
    assert not policy.decide(now=night, last_ask_at=None).allowed
    assert policy.decide(now=day, last_ask_at=None).allowed


def test_quiet_hours_daytime_window():
    policy = ProactiveAskPolicy(ProactiveAskConfig(quiet_start_hour=9, quiet_end_hour=17))
    assert not policy.decide(now=NOON.replace(hour=10), last_ask_at=None).allowed
    assert policy.decide(now=NOON.replace(hour=20), last_ask_at=None).allowed


def test_equal_quiet_bounds_means_no_quiet_window():
    policy = ProactiveAskPolicy(ProactiveAskConfig(quiet_start_hour=9, quiet_end_hour=9))
    assert policy.decide(now=NOON.replace(hour=9), last_ask_at=None).allowed


# --- policy: rate limit + enabled ------------------------------------------


def test_rate_limit_blocks_within_interval():
    policy = ProactiveAskPolicy(ProactiveAskConfig(min_interval_minutes=60))
    recent = NOON - timedelta(minutes=30)
    assert not policy.decide(now=NOON, last_ask_at=recent).allowed
    old = NOON - timedelta(minutes=90)
    assert policy.decide(now=NOON, last_ask_at=old).allowed


def test_disabled_policy_never_asks():
    policy = ProactiveAskPolicy(ProactiveAskConfig(enabled=False))
    decision = policy.decide(now=NOON, last_ask_at=None)
    assert not decision.allowed and decision.reason == "disabled"


# --- policy: consent (contract C6) -----------------------------------------


def test_consent_required_but_missing_callback_blocks():
    policy = ProactiveAskPolicy(ProactiveAskConfig(require_consent=True))
    assert not policy.decide(now=NOON, last_ask_at=None, consent_fn=None).allowed


def test_consent_granted_and_denied():
    policy = ProactiveAskPolicy(ProactiveAskConfig(require_consent=True))
    grant = policy.decide(
        now=NOON, last_ask_at=None, consent_fn=lambda *a, **k: "once"
    )
    deny = policy.decide(
        now=NOON, last_ask_at=None, consent_fn=lambda *a, **k: "deny"
    )
    assert grant.allowed
    assert not deny.allowed and deny.reason == "no_consent"


# --- question wording -------------------------------------------------------


def test_build_question_varies_by_gap_reason():
    assert "no measurable" in build_question(_gap("no_metric"))
    assert "no target" in build_question(_gap("unmeasured_target", "cov"))
    assert "current value" in build_question(_gap("stale", "cov"))


# --- monitor orchestration --------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_asks_and_logs_when_allowed():
    store = FakeStore([_gap("no_metric")])
    delivered: List[str] = []
    monitor = ProactiveMeasurementMonitor(
        store,
        policy=ProactiveAskPolicy(ProactiveAskConfig()),
        ask_fn=lambda principal, q: delivered.append(q),
    )
    outcomes = await monitor.run_once(_principal(), now=NOON)
    assert len(outcomes) == 1 and outcomes[0].asked
    assert delivered and store.logged  # delivered via ask_fn AND audited


@pytest.mark.asyncio
async def test_monitor_respects_policy_block():
    store = FakeStore([_gap("no_metric")])
    delivered: List[str] = []
    monitor = ProactiveMeasurementMonitor(
        store,
        policy=ProactiveAskPolicy(ProactiveAskConfig(enabled=False)),
        ask_fn=lambda principal, q: delivered.append(q),
    )
    outcomes = await monitor.run_once(_principal(), now=NOON)
    assert outcomes and not outcomes[0].asked
    assert not delivered and not store.logged  # nothing sent, nothing logged


@pytest.mark.asyncio
async def test_monitor_rate_limits_second_ask_within_run():
    store = FakeStore([_gap("no_metric", None), _gap("stale", "cov")])
    delivered: List[str] = []
    monitor = ProactiveMeasurementMonitor(
        store,
        policy=ProactiveAskPolicy(ProactiveAskConfig(min_interval_minutes=60)),
        ask_fn=lambda principal, q: delivered.append(q),
    )
    outcomes = await monitor.run_once(_principal(), now=NOON)
    asked = [o for o in outcomes if o.asked]
    assert len(asked) == 1  # first ask consumes the rate-limit window
    assert len(delivered) == 1
