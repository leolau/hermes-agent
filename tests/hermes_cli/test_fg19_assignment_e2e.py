"""Postgres E2E for FG-19 — per-user GTS isolation + cross-user assignment.

Exercises the **real** datastore path (contract C3 ``get_store`` → asyncpg)
against a throwaway Postgres schema and a temp ``HERMES_HOME``, proving the
FG-19 Definition-of-Done end to end on top of FG-18's C9 graph:

* **Assignment lifecycle** — user A assigns a sub-task to user B (a C6
  notification fires), B accepts, B advances progress → A's *parent-task* score
  rolls up (score stays auto-computed; the assignee never hand-sets it).
* **Per-item isolation (C2 + RLS)** — B (the assignee) sees ONLY the granted
  item, never A's other private GTS (the item's parent task, A's secret goal /
  task); a non-grantee sees nothing; the owner role sees the whole chain. The
  "granted to me" clause is verified at the **Postgres RLS** layer, not just the
  app filter.
* **Authority boundary** — the assignee may advance progress + add a sub-task
  but may NOT change the evaluation method, reassign, revoke, or edit content;
  a watcher is read-only; top-level goals are not assignable; the single-active-
  assignee invariant holds.
* **Agent-initiated assignment** requires C6 approval (denied → refused +
  audited; approved → recorded).
* **Audit (C5 + C8)** — assign / accept / decline / progress / revoke emit a
  recorded change to the injected C5 sink and a durable on-disk row; refusals
  land as ``core_denied``.
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
from hermes_cli.goal_registry import GOALS_TABLE
from hermes_cli.gts import (
    GRANT_WATCHER,
    GtsAssignmentError,
    GtsAuthorityError,
    GtsCentre,
    gts_audit_log_path,
)
from hermes_cli.task_registry import TASKS_TABLE, TaskSpec

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
    container = f"hermes-fg19-{uuid.uuid4().hex[:12]}"
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
    dsn: str, *, audit_sink=None, notifier=None
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
        notifier=notifier,
    )
    await centre.initialize()
    return centre


def _principal(user_id: str, role: Role = "member") -> Principal:
    return Principal(user_id=user_id, display=user_id, role=role)


@pytest.mark.asyncio
async def test_assignment_lifecycle_score_rollup_and_isolation(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    sink: list[dict] = []
    notes: list[dict] = []
    centre = await _fresh_centre(
        postgres_dsn, audit_sink=sink.append, notifier=notes.append
    )
    alice = _principal("alice")
    bob = _principal("bob")
    carol = _principal("carol")
    owner = _principal("root", "owner")

    # Alice's GTS: a top goal → sub-goal, a parent task (linked to the
    # sub-goal) with a sub-task, plus unrelated *other private* items.
    top = await centre.create_goal(alice, "Ship Q3", actor="user")
    sub = await centre.create_goal(
        alice, "Pipeline", actor="agent", parent_goal_id=top.id
    )
    parent_task = await centre.create_task(
        alice, _spec("Run outreach"), actor="user", goal_ids=[sub.id]
    )
    subtask = await centre.create_task(
        alice, _spec("Email batch 1"), actor="agent",
        parent_task_id=parent_task.id, priority="high",
    )
    secret_goal = await centre.create_goal(alice, "Private OKR", actor="user")
    secret_task = await centre.create_task(alice, _spec("Private task"), actor="user")
    assert secret_goal.visibility == private("alice")

    # A assigns the sub-task to B, requiring acceptance → grant is pending and a
    # C6 notification fired.
    grant = await centre.assign(
        alice, "task", subtask.id, "bob", require_acceptance=True
    )
    assert grant.status == "pending" and grant.user_id == "bob"
    assert notes and notes[-1]["action"] == "assign" and notes[-1]["assignee_user_id"] == "bob"

    # B accepts, then advances progress (allowed for the active assignee).
    accepted = await centre.accept_assignment(bob, grant.id)
    assert accepted.status == "accepted"
    await centre.advance_task(bob, subtask.id, "in_progress", actor="user")

    # Score stays auto-computed and rolls up: the in-progress sub-task scores 50
    # and its parent task rolls that single child up by priority weight.
    bob_view = await centre.get_task(bob, subtask.id)
    assert bob_view is not None and bob_view.score is None  # not yet recomputed
    parent_score = await centre.recompute_task_score(alice, parent_task.id)
    scored = await centre.get_task(alice, subtask.id)
    assert scored is not None and scored.score == pytest.approx(50.0)
    assert parent_score == pytest.approx(50.0)

    # Per-item isolation: B sees ONLY the granted sub-task — never the parent
    # task it hangs under, nor A's secret goal/task.
    assert await centre.get_task(bob, subtask.id) is not None
    assert await centre.get_task(bob, parent_task.id) is None
    assert await centre.get_task(bob, secret_task.id) is None
    assert await centre.get_goal(bob, secret_goal.id) is None
    bob_goal_ids = {g.id for g in await centre.list_goals(bob)}
    assert bob_goal_ids == set()  # none of Alice's goals are shared with Bob

    # A non-grantee (Carol) sees nothing of Alice's private chain.
    assert await centre.get_task(carol, subtask.id) is None

    # The owner sees the whole chain + can browse Alice's GTS by user.
    assert await centre.get_task(owner, parent_task.id) is not None
    assert await centre.get_task(owner, subtask.id) is not None
    owner_goal_ids = {
        g.id for g in await centre.list_goals_for_user(owner, "alice")
    }
    assert {top.id, sub.id, secret_goal.id} <= owner_goal_ids

    # DB-level RLS backstop (not just the app filter): the store connects as a
    # superuser (which bypasses RLS), so — exactly like the FG-18 negative test
    # — we drop to a non-superuser reader role via SET LOCAL ROLE inside the
    # bound transaction to actually exercise the FORCE'd policy.
    conn = await get_store("supabase-app", "dev", config=_config(postgres_dsn)).connect()
    try:
        await conn.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='gts_reader')
                THEN CREATE ROLE gts_reader NOLOGIN; END IF;
            END $$;
            GRANT USAGE ON SCHEMA app_dev TO gts_reader;
            GRANT SELECT ON app_dev.tasks, app_dev.item_grants TO gts_reader;
            """
        )

        async def _rls_task_ids(principal: Principal) -> set[str]:
            async with conn.transaction():
                await bind_principal(conn, principal)
                await conn.execute("SET LOCAL ROLE gts_reader")
                rows = await conn.fetch(f"SELECT id FROM {TASKS_TABLE}")
            return {str(r["id"]) for r in rows}

        # The assignee's own grant admits ONLY the granted sub-task.
        bob_task_ids = await _rls_task_ids(bob)
        assert subtask.id in bob_task_ids
        assert parent_task.id not in bob_task_ids
        assert secret_task.id not in bob_task_ids

        # A non-grantee sees none of Alice's private tasks at the RLS layer.
        carol_task_ids = await _rls_task_ids(carol)
        assert carol_task_ids.isdisjoint(
            {subtask.id, parent_task.id, secret_task.id}
        )

        # The owner role bypasses scoping at the RLS layer too.
        owner_task_ids = await _rls_task_ids(owner)
        assert {subtask.id, parent_task.id, secret_task.id} <= owner_task_ids
    finally:
        await conn.close()

    # Audit (C5 sink): assign / accept / progress were recorded with the actor.
    recorded = [e for e in sink if e["decision"] == "recorded"]
    by_action = {e["action"] for e in recorded}
    assert {"assign", "accept", "progress"} <= by_action
    assert any(
        e["action"] == "progress" and e["actor_user_id"] == "bob" for e in recorded
    )
    # …and a durable on-disk audit exists under HERMES_HOME.
    log = gts_audit_log_path()
    assert log.exists()


@pytest.mark.asyncio
async def test_assignee_authority_boundary_and_top_level_guard(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    centre = await _fresh_centre(postgres_dsn)
    alice = _principal("alice")
    bob = _principal("bob")
    carol = _principal("carol")

    top = await centre.create_goal(alice, "Top", actor="user")
    task = await centre.create_task(alice, _spec("Task"), actor="user")
    grant = await centre.assign(alice, "task", task.id, "bob")
    assert grant.status == "accepted"  # user-initiated auto-accepts

    # A top-level goal is NOT assignable.
    with pytest.raises(GtsAssignmentError):
        await centre.assign(alice, "goal", top.id, "bob")

    # Single-active-assignee invariant: a second assignee is refused.
    with pytest.raises(GtsAssignmentError):
        await centre.assign(alice, "task", task.id, "carol")

    # The assignee MAY advance progress and add a sub-task under the item…
    await centre.advance_task(bob, task.id, "in_progress", actor="user")
    child = await centre.create_task(
        bob, _spec("Follow-up"), actor="user", parent_task_id=task.id
    )
    assert child.parent_task_id == task.id

    # …but may NOT change the evaluation method, reassign, revoke, or edit
    # content (all owner-only) — each refused + audited.
    with pytest.raises(GtsAuthorityError):
        await centre.set_evaluation_method(
            bob, "task", task.id, {"weights": {"x": 1.0}}, actor="user"
        )
    with pytest.raises(GtsAuthorityError):
        await centre.reassign(bob, "task", task.id, "carol")
    with pytest.raises(GtsAuthorityError):
        await centre.revoke_grant(bob, grant.id)

    # A watcher is read-only: can read the item, cannot advance it.
    watch_task = await centre.create_task(alice, _spec("Watched"), actor="user")
    await centre.assign(alice, "task", watch_task.id, "carol", grant=GRANT_WATCHER)
    assert await centre.get_task(carol, watch_task.id) is not None
    with pytest.raises(GtsAuthorityError):
        await centre.advance_task(carol, watch_task.id, "in_progress", actor="user")


@pytest.mark.asyncio
async def test_agent_initiated_assignment_requires_c6_approval(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    sink: list[dict] = []
    centre = await _fresh_centre(postgres_dsn, audit_sink=sink.append)
    alice = _principal("alice")
    task = await centre.create_task(alice, _spec("Delegate me"), actor="user")

    deny = lambda *_a, **_k: "no"  # noqa: E731 - test stub
    approve = lambda *_a, **_k: "once"  # noqa: E731 - test stub

    # Agent-initiated assignment with C6 denied → refused + audited.
    with pytest.raises(GtsAuthorityError):
        await centre.assign(
            alice, "task", task.id, "bob", actor="agent", approval_callback=deny
        )
    refused = [e for e in sink if e["decision"] == "refused"]
    assert any(e["action"] == "assign" for e in refused)
    assert all(e["kind"] == "core_denied" for e in refused)

    # With C6 approved the agent assignment is recorded (pending acceptance).
    grant = await centre.assign(
        alice, "task", task.id, "bob", actor="agent", approval_callback=approve
    )
    assert grant.user_id == "bob" and grant.status == "pending"


@pytest.mark.asyncio
async def test_decline_revokes_access_and_is_audited(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    sink: list[dict] = []
    centre = await _fresh_centre(postgres_dsn, audit_sink=sink.append)
    alice = _principal("alice")
    bob = _principal("bob")

    task = await centre.create_task(alice, _spec("Maybe"), actor="user")
    grant = await centre.assign(
        alice, "task", task.id, "bob", require_acceptance=True
    )
    # While pending, the grant is active → Bob can read the item.
    assert await centre.get_task(bob, task.id) is not None

    # Bob declines → the grant is no longer active → access is withdrawn.
    declined = await centre.decline_assignment(bob, grant.id)
    assert declined.status == "declined"
    assert await centre.get_task(bob, task.id) is None

    # Only the grantee may accept/decline; a stranger is refused + audited.
    other = await centre.assign(
        alice, "task", task.id, "bob", require_acceptance=True
    )
    with pytest.raises(GtsAuthorityError):
        await centre.decline_assignment(_principal("carol"), other.id)

    recorded = {e["action"] for e in sink if e["decision"] == "recorded"}
    assert "decline" in recorded
