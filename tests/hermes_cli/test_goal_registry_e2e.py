"""Postgres E2E for the FG-04 prioritised, measurable goal registry.

Exercises the **real** datastore path (contract C3 ``get_store`` → asyncpg)
against a throwaway Postgres schema and a temp ``HERMES_HOME``:

* C2 visibility scoping + the mandatory **negative-access** test — a
  ``private:<other>`` goal (and its metrics/progress) is invisible to a
  different member and visible to the owner — enforced both at the app layer
  (``scope_filter``) and by Postgres **RLS** on ``goals``.
* The full measurement loop: create a goal with no metric → the proactive
  monitor detects the gap and asks via a cache-safe appended message (C6
  gated) → the answer sets the target → a measurement records progress →
  ``verdict_for_metrics`` on the stored metrics flips to ``done`` and the
  judge-facing block renders the number.
* Priority scheduling over the persisted registry.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from hermes_cli import goal_registry
from hermes_cli.access import Principal, Role, bind_principal, private
from hermes_cli.datastore import get_store
from hermes_cli.goal_monitor import (
    ProactiveAskConfig,
    ProactiveAskPolicy,
    ProactiveMeasurementMonitor,
)
from hermes_cli.goal_registry import (
    GOALS_TABLE,
    GoalRegistryStore,
    schedule_turn_budget,
)
from hermes_cli.goals import (
    GoalMetric,
    render_metrics_block,
    verdict_for_metrics,
)

NOON = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the Postgres E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the Postgres E2E test")

    image = (
        "postgres@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    container = f"hermes-fg04-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--env", "POSTGRES_PASSWORD=hermes-test",
            "--env", "POSTGRES_DB=hermes_test",
            "--publish", "127.0.0.1::5432",
            image,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        port_result = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            check=True,
            capture_output=True,
            text=True,
        )
        port = port_result.stdout.strip().rsplit(":", 1)[1]
        dsn = f"postgresql://postgres:hermes-test@127.0.0.1:{port}/hermes_test"
        for _ in range(60):
            try:
                asyncio.run(_probe_postgres(dsn))
                break
            except (OSError, asyncpg.PostgresError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Throwaway Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False,
            capture_output=True,
        )


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


async def _fresh_store(dsn: str) -> GoalRegistryStore:
    """Drop + recreate the app_dev schema and initialise the registry."""
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS app_dev CASCADE")
    finally:
        await conn.close()
    store = GoalRegistryStore(
        get_store("supabase-app", "dev", config=_config(dsn))
    )
    await store.initialize()
    return store


def _principal(user_id: str, role: Role = "member") -> Principal:
    return Principal(user_id=user_id, display=user_id, role=role)


@pytest.mark.asyncio
async def test_create_scope_and_negative_access(postgres_dsn: str) -> None:
    store = await _fresh_store(postgres_dsn)
    alice = _principal("alice")
    bob = _principal("bob")
    owner = _principal("root", "owner")

    shared = await store.create_goal(alice, "Ship launch", visibility="shared")
    secret = await store.create_goal(alice, "Personal OKR")  # private:alice
    assert secret.visibility == private("alice")

    # Alice attaches a metric + progress to her private goal.
    await store.add_metric(alice, secret.id, GoalMetric("focus_hours", target=40))
    await store.record_progress(alice, secret.id, value=5, note="week 1")

    # Negative access (app layer): a different member sees only the shared goal.
    bob_ids = {g.id for g in await store.list_goals(bob)}
    assert shared.id in bob_ids
    assert secret.id not in bob_ids
    assert await store.get_goal(bob, secret.id) is None
    assert await store.list_metrics(bob, secret.id) == []  # metrics hidden too
    assert await store.list_progress(bob, secret.id) == []

    # Owner bypasses private scoping and sees everything.
    owner_ids = {g.id for g in await store.list_goals(owner)}
    assert {shared.id, secret.id} <= owner_ids
    assert await store.get_goal(owner, secret.id) is not None


@pytest.mark.asyncio
async def test_rls_backstop_enforces_scope_at_the_database(postgres_dsn: str) -> None:
    """DB-level RLS backstop on ``goals`` (not just the app-layer filter)."""
    store = await _fresh_store(postgres_dsn)
    await store.create_goal(_principal("root", "owner"), "Org goal", visibility="shared")
    await store.create_goal(_principal("alice"), "Alice private")
    await store.create_goal(_principal("bob"), "Bob private")

    conn = await get_store("supabase-app", "dev", config=_config(postgres_dsn)).connect()
    try:
        await conn.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='goal_reader')
                THEN CREATE ROLE goal_reader NOLOGIN; END IF;
            END $$;
            GRANT USAGE ON SCHEMA app_dev TO goal_reader;
            GRANT SELECT ON app_dev.goals TO goal_reader;
            """
        )

        async def visible_titles(user_id: str, role: Role) -> set:
            async with conn.transaction():
                await bind_principal(conn, _principal(user_id, role))
                await conn.execute("SET LOCAL ROLE goal_reader")
                rows = await conn.fetch(f"SELECT title FROM {GOALS_TABLE}")
                return {r["title"] for r in rows}

        assert await visible_titles("alice", "member") == {"Org goal", "Alice private"}
        assert await visible_titles("bob", "member") == {"Org goal", "Bob private"}
        assert await visible_titles("root", "owner") == {
            "Org goal", "Alice private", "Bob private",
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_measurement_loop_end_to_end(postgres_dsn: str) -> None:
    store = await _fresh_store(postgres_dsn)
    alice = _principal("alice")
    goal = await store.create_goal(alice, "Grow newsletter", priority="high")

    # No metric yet → monitor finds a gap and (policy-permitting) asks via an
    # appended, cache-safe message. We capture the ask instead of mutating any
    # prompt.
    asked: list[str] = []
    monitor = ProactiveMeasurementMonitor(
        store,
        policy=ProactiveAskPolicy(ProactiveAskConfig()),
        ask_fn=lambda principal, q: asked.append(q),
    )
    outcomes = await monitor.run_once(alice, now=NOON)
    assert any(o.asked for o in outcomes)
    assert asked and "newsletter" in asked[0].lower()

    # The user answers: define the metric and its target (the "answer" path).
    await store.add_metric(
        alice, goal.id, GoalMetric("subscribers", unit="people")
    )
    await store.set_metric_target(alice, goal.id, "subscribers", 1000, unit="people")

    metrics = await store.list_metrics(alice, goal.id)
    assert verdict_for_metrics(metrics)[0] == "continue"  # target set, not met

    # A measurement records progress history and updates current.
    await store.set_metric_value(alice, goal.id, "subscribers", 400, note="launch")
    await store.set_metric_value(alice, goal.id, "subscribers", 1000, note="hit goal")
    history = await store.list_progress(alice, goal.id)
    assert [h["value"] for h in history] == [400, 1000]  # append-only, ordered

    # Achievement is now computed from the stored metric — the judge-facing
    # block renders the number, and the verdict flips to done.
    metrics = await store.list_metrics(alice, goal.id)
    assert verdict_for_metrics(metrics) == ("done", "all metrics achieved")
    block = render_metrics_block(metrics)
    assert "1000" in block and "ACHIEVED" in block


@pytest.mark.asyncio
async def test_stale_metric_is_solicited_but_quiet_hours_blocks(
    postgres_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = await _fresh_store(postgres_dsn)
    alice = _principal("alice")
    goal = await store.create_goal(alice, "Weekly review")
    # Pin the measurement clock so ``last_measured_at`` is anchored to NOON
    # rather than the real wall clock; otherwise staleness (below) depends on
    # how far the current date has drifted past NOON.
    monkeypatch.setattr(goal_registry, "_utcnow", lambda: NOON)
    await store.add_metric(
        alice, goal.id, GoalMetric("reviews", target=1, current=1, cadence="1h")
    )

    # Far in the future the 1h-cadence metric is stale → a gap exists…
    future = NOON + timedelta(days=3)
    gaps = await store.measurement_gaps(alice, now=future)
    assert any(g.reason == "stale" for g in gaps)

    # …but a quiet-hours policy suppresses the ask (C6) and logs nothing.
    asked: list[str] = []
    monitor = ProactiveMeasurementMonitor(
        store,
        policy=ProactiveAskPolicy(
            ProactiveAskConfig(quiet_start_hour=0, quiet_end_hour=23)
        ),
        ask_fn=lambda principal, q: asked.append(q),
    )
    outcomes = await monitor.run_once(alice, now=future.replace(hour=3))
    assert outcomes and not any(o.asked for o in outcomes)
    assert not asked
    assert await store.last_ask_at("alice") is None


@pytest.mark.asyncio
async def test_priority_scheduling_over_persisted_registry(postgres_dsn: str) -> None:
    store = await _fresh_store(postgres_dsn)
    alice = _principal("alice")
    await store.create_goal(alice, "Critical thing", priority="critical", visibility="shared")
    await store.create_goal(alice, "Low thing", priority="low", visibility="shared")
    await store.create_goal(alice, "Medium thing", priority="medium", visibility="shared")

    goals = await store.list_goals(alice, status="active")
    # Persisted goals come back priority-ordered.
    assert [g.priority for g in goals] == ["critical", "medium", "low"]

    alloc = schedule_turn_budget(goals, 12)
    by_priority = {g.priority: alloc[g.id] for g in goals}
    assert sum(alloc.values()) == 12
    assert by_priority["critical"] >= by_priority["medium"] >= by_priority["low"]
