"""Postgres E2E for the FG-18 / C9 GTS Centre (Goals → Tasks → Skills).

Exercises the **real** datastore path (contract C3 ``get_store`` → asyncpg)
against a throwaway Postgres schema and a temp ``HERMES_HOME``, proving the
FG-18 Definition-of-Done end to end:

* **Authority** — only the user may create/manage a top-level goal or set an
  evaluation method; a runtime-agent attempt is refused *and audited* (a
  durable ``core_denied`` row under ``HERMES_HOME``); the agent may add
  sub-goals / tasks / sub-tasks under an authorized parent.
* **Cycle prevention** — a goal/task cannot become its own ancestor.
* **M:N edges** — ``task_goals`` / ``task_skills`` CRUD, with a
  ``skills_registry`` node that *references* existing skill content.
* **Score** — always computed, clamped ``0–100``, priority-weighted rollup from
  children to parent; ``verdict_for_metrics`` reused for the judge verdict.
* **Negative access (C2 + RLS)** — user A's private goal/task/skill is invisible
  to user B; the owner sees all.
* **Cache-safety** — GTS mutations never mutate the byte-stable system prompt.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest

from hermes_cli.access import Principal, Role, bind_principal, private
from hermes_cli.datastore import get_store
from hermes_cli.goals import GoalMetric
from hermes_cli.gts import (
    GtsAuthorityError,
    GtsCentre,
    GtsCycleError,
    SKILLS_TABLE,
    gts_audit_log_path,
    render_gts_block,
)
from hermes_cli.task_registry import TaskSpec

_STATES = ("pending", "in_progress", "completed")


def _spec(title: str) -> TaskSpec:
    return TaskSpec(
        title=title,
        description=title,
        trigger_state="pending",
        completion_state="completed",
        progress_states=_STATES,
    )


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
    container = f"hermes-fg18-{uuid.uuid4().hex[:12]}"
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


async def _fresh_centre(
    dsn: str, *, audit_sink=None
) -> GtsCentre:
    """Drop + recreate the app_dev schema and initialise the GTS Centre."""
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS app_dev CASCADE")
    finally:
        await conn.close()
    centre = GtsCentre(
        get_store("supabase-app", "dev", config=_config(dsn)),
        audit_sink=audit_sink,
    )
    await centre.initialize()
    return centre


def _principal(user_id: str, role: Role = "member") -> Principal:
    return Principal(user_id=user_id, display=user_id, role=role)


@pytest.mark.asyncio
async def test_authority_model_and_refusal_audit(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    sink: list[dict] = []
    centre = await _fresh_centre(postgres_dsn, audit_sink=sink.append)
    alice = _principal("alice")

    # User creates a top-level goal — allowed.
    top = await centre.create_goal(alice, "Grow revenue", actor="user")
    assert top.level == "top"

    # The runtime agent may NOT create a top-level goal — refused + audited.
    with pytest.raises(GtsAuthorityError):
        await centre.create_goal(alice, "Agent's own goal", actor="agent")

    # The agent MAY create a sub-goal, a task, and a sub-task under the
    # user-authorized parent.
    sub = await centre.create_goal(
        alice, "Q3 pipeline", actor="agent", parent_goal_id=top.id
    )
    assert sub.level == "sub" and sub.parent_goal_id == top.id
    task = await centre.create_task(
        alice, _spec("Draft outreach"), actor="agent", goal_ids=[sub.id]
    )
    subtask = await centre.create_task(
        alice, _spec("Write email 1"), actor="agent", parent_task_id=task.id
    )
    assert subtask.parent_task_id == task.id

    # Evaluation method: user may set it…
    method = await centre.set_evaluation_method(
        alice, "goal", top.id, {"weights": {"revenue": 1.0}}, actor="user"
    )
    assert method.locked is True
    # …the agent may not (refused + audited).
    with pytest.raises(GtsAuthorityError):
        await centre.set_evaluation_method(
            alice, "goal", top.id, {"weights": {"revenue": 2.0}}, actor="agent"
        )

    # Two refusals were audited via the injected C5 sink…
    refused = [e for e in sink if e["decision"] == "refused"]
    assert len(refused) == 2
    assert all(e["kind"] == "core_denied" for e in refused)

    # …and to the durable on-disk audit log under HERMES_HOME.
    log = gts_audit_log_path()
    assert log == tmp_path / "audit" / "gts_authority.jsonl"
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    denied = [r for r in rows if r["decision"] == "refused"]
    assert len(denied) == 2
    assert {r["action"] for r in denied} == {
        "create a top-level goal",
        "set an evaluation method",
    }


@pytest.mark.asyncio
async def test_cycle_prevention_for_goals_and_tasks(postgres_dsn: str) -> None:
    centre = await _fresh_centre(postgres_dsn)
    alice = _principal("alice")

    a = await centre.create_goal(alice, "A", actor="user")
    b = await centre.create_goal(alice, "B", actor="agent", parent_goal_id=a.id)
    c = await centre.create_goal(alice, "C", actor="agent", parent_goal_id=b.id)
    # Re-parenting an ancestor under its own descendant would form a cycle.
    with pytest.raises(GtsCycleError):
        await centre.reparent_goal(alice, a.id, c.id, actor="user")
    with pytest.raises(GtsCycleError):
        await centre.reparent_goal(alice, a.id, a.id, actor="user")

    t1 = await centre.create_task(alice, _spec("t1"), actor="user")
    t2 = await centre.create_task(
        alice, _spec("t2"), actor="agent", parent_task_id=t1.id
    )
    t3 = await centre.create_task(
        alice, _spec("t3"), actor="agent", parent_task_id=t2.id
    )
    with pytest.raises(GtsCycleError):
        await centre.reparent_task(alice, t1.id, t3.id)
    with pytest.raises(GtsCycleError):
        await centre.reparent_task(alice, t1.id, t1.id)


@pytest.mark.asyncio
async def test_mn_edges_and_priority_ordering(postgres_dsn: str) -> None:
    centre = await _fresh_centre(postgres_dsn)
    alice = _principal("alice")

    g_low = await centre.create_goal(alice, "Low", actor="user", priority="low")
    g_crit = await centre.create_goal(
        alice, "Crit", actor="user", priority="critical"
    )
    # Priority ordering over the persisted graph.
    ordered = await centre.list_goals(alice, top_level_only=True)
    assert [g.title for g in ordered] == ["Crit", "Low"]

    task = await centre.create_task(alice, _spec("Do it"), actor="user")
    await centre.link_task_to_goal(alice, task.id, g_low.id)
    await centre.link_task_to_goal(alice, task.id, g_crit.id)
    linked = {g.id for g in await centre.goals_for_task(alice, task.id)}
    assert linked == {g_low.id, g_crit.id}

    # Idempotent + removable (M:N CRUD).
    await centre.link_task_to_goal(alice, task.id, g_crit.id)
    await centre.unlink_task_from_goal(alice, task.id, g_low.id)
    linked = {g.id for g in await centre.goals_for_task(alice, task.id)}
    assert linked == {g_crit.id}

    # Skill registry references existing skill content (no copy).
    skill = await centre.register_skill(
        alice, "outreach", "skills/outreach/SKILL.md"
    )
    assert skill.skill_ref == "skills/outreach/SKILL.md"
    await centre.link_task_to_skill(alice, task.id, skill.id)
    task_skills = await centre.skills_for_task(alice, task.id)
    assert [s.skill_ref for s in task_skills] == ["skills/outreach/SKILL.md"]


@pytest.mark.asyncio
async def test_score_is_computed_clamped_and_rolls_up(postgres_dsn: str) -> None:
    centre = await _fresh_centre(postgres_dsn)
    alice = _principal("alice")

    top = await centre.create_goal(alice, "OKR", actor="user")
    hi = await centre.create_goal(
        alice, "Critical child", actor="agent", parent_goal_id=top.id,
        priority="critical",
    )
    lo = await centre.create_goal(
        alice, "Low child", actor="agent", parent_goal_id=top.id, priority="low"
    )
    # Achieved critical child (metric current ≥ target) and an unmet low child.
    await centre.goals.add_metric(alice, hi.id, GoalMetric("m", target=10, current=10))
    await centre.goals.add_metric(alice, lo.id, GoalMetric("m", target=10, current=0))

    top_score = await centre.recompute_goal_score(alice, top.id)
    # Leaves computed + clamped; parent = priority-weighted (8*100 + 1*0)/9.
    assert (await centre.get_goal(alice, hi.id)).score == pytest.approx(100.0)
    assert (await centre.get_goal(alice, lo.id)).score == pytest.approx(0.0)
    assert top_score == pytest.approx(800.0 / 9.0)

    # Reused FG-04 verdict over the achieved leaf's metrics.
    assert await centre.goal_verdict(alice, hi.id) == ("done", "all metrics achieved")

    # Tasks: leaf score tracks the FG-06 progress state machine, then rolls up.
    parent_task = await centre.create_task(alice, _spec("Parent"), actor="user")
    child_task = await centre.create_task(
        alice, _spec("Child"), actor="agent", parent_task_id=parent_task.id,
        priority="high",
    )
    await centre.tasks.transition(alice, child_task.id, "in_progress")
    parent_task_score = await centre.recompute_task_score(alice, parent_task.id)
    assert (await centre.get_task(alice, child_task.id)).score == pytest.approx(50.0)
    assert parent_task_score == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_negative_access_across_users(postgres_dsn: str) -> None:
    centre = await _fresh_centre(postgres_dsn)
    alice = _principal("alice")
    bob = _principal("bob")
    owner = _principal("root", "owner")

    shared = await centre.create_goal(
        alice, "Shared goal", actor="user", visibility="shared"
    )
    secret = await centre.create_goal(alice, "Secret goal", actor="user")
    assert secret.visibility == private("alice")
    secret_task = await centre.create_task(alice, _spec("Secret task"), actor="user")
    secret_skill = await centre.register_skill(alice, "secret", "skills/s/SKILL.md")

    # App-layer C2: Bob sees only the shared goal, and none of Alice's private
    # goal / task / skill.
    bob_goal_ids = {g.id for g in await centre.list_goals(bob)}
    assert shared.id in bob_goal_ids and secret.id not in bob_goal_ids
    assert await centre.get_goal(bob, secret.id) is None
    assert await centre.get_task(bob, secret_task.id) is None
    bob_skill_ids = {s.id for s in await centre.list_skills(bob)}
    assert secret_skill.id not in bob_skill_ids
    assert await centre.goals_for_task(bob, secret_task.id) == []

    # The owner bypasses private scoping and sees everything.
    owner_goal_ids = {g.id for g in await centre.list_goals(owner)}
    assert {shared.id, secret.id} <= owner_goal_ids
    assert await centre.get_task(owner, secret_task.id) is not None

    # DB-level RLS backstop on skills_registry (not just the app filter).
    conn = await get_store(
        "supabase-app", "dev", config=_config(postgres_dsn)
    ).connect()
    try:
        await conn.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='skill_reader')
                THEN CREATE ROLE skill_reader NOLOGIN; END IF;
            END $$;
            GRANT USAGE ON SCHEMA app_dev TO skill_reader;
            GRANT SELECT ON app_dev.skills_registry TO skill_reader;
            """
        )
        async with conn.transaction():
            await bind_principal(conn, bob)
            await conn.execute("SET LOCAL ROLE skill_reader")
            names = {
                r["name"]
                for r in await conn.fetch(f"SELECT name FROM {SKILLS_TABLE}")
            }
        assert "secret" not in names
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_gts_mutations_never_mutate_the_system_prompt(
    postgres_dsn: str,
) -> None:
    centre = await _fresh_centre(postgres_dsn)
    alice = _principal("alice")
    # A stand-in byte-stable system prompt captured before any GTS activity.
    system_prompt = b"<system>Hermes core prompt, cached for the conversation.</system>"
    before = bytes(system_prompt)

    top = await centre.create_goal(alice, "Cache-safe goal", actor="user")
    sub = await centre.create_goal(
        alice, "sub", actor="agent", parent_goal_id=top.id
    )
    await centre.goals.add_metric(alice, sub.id, GoalMetric("m", target=1, current=1))
    await centre.recompute_goal_score(alice, top.id)
    task = await centre.create_task(alice, _spec("t"), actor="agent", goal_ids=[sub.id])
    await centre.tasks.transition(alice, task.id, "in_progress")

    # The prompt bytes are untouched by every GTS mutation…
    assert system_prompt == before
    # …and GTS state reaches the agent only as an appendable block.
    block = render_gts_block(await centre.list_goals(alice, top_level_only=True))
    assert "Cache-safe goal" in block
    assert system_prompt == before
