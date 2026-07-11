from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import asyncpg
import pytest

from gateway.session import Platform, SessionSource, build_session_key
from hermes_cli.access import Principal, Role, bind_principal
from hermes_cli.changes import ChangeLog, initialize_changes
from hermes_cli.consent import ConsentPolicy
from hermes_cli.datastore import get_store
from hermes_cli.task_registry import (
    LiveMemoryIntentSignals,
    TASKS_TABLE,
    TaskDiscoveryEngine,
    TaskRegistryStore,
    TaskSpec,
)
from plugins.memory.supabase_pgvector.store import PgvectorMemoryStore


_PGVECTOR_IMAGE = (
    "pgvector/pgvector@sha256:"
    "1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the task discovery E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the task discovery E2E test")

    subprocess.run(
        ["docker", "pull", _PGVECTOR_IMAGE],
        check=True,
        capture_output=True,
    )
    container = f"hermes-fg06-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--env",
            "POSTGRES_PASSWORD=hermes-test",
            "--env",
            "POSTGRES_DB=hermes_test",
            "--publish",
            "127.0.0.1::5432",
            _PGVECTOR_IMAGE,
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
        dsn = (
            "postgresql://postgres:hermes-test"
            f"@127.0.0.1:{port}/hermes_test"
        )
        for _ in range(60):
            try:
                asyncio.run(_probe_postgres(dsn))
                break
            except (OSError, asyncpg.PostgresError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Throwaway pgvector Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False,
            capture_output=True,
        )


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


async def _fresh_stores(dsn: str):
    connection = await asyncpg.connect(dsn, ssl=False)
    try:
        await connection.execute(
            "DROP SCHEMA IF EXISTS app_dev CASCADE;"
            "DROP SCHEMA IF EXISTS app_prod CASCADE;"
        )
        await initialize_changes(connection)
    finally:
        await connection.close()

    app_store = get_store("supabase-app", "prod", config=_config(dsn))
    memory = PgvectorMemoryStore(app_store)
    registry = TaskRegistryStore(app_store)
    await memory.initialize()
    await registry.initialize()
    return app_store, memory, registry


@pytest.mark.asyncio
async def test_repeated_intent_to_approved_completed_task_real_path(
    postgres_dsn: str,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    app_store, memory, registry = await _fresh_stores(postgres_dsn)
    alice = Principal(user_id="alice", display="Alice", role="member")
    proposals: list[str] = []
    change_log = ChangeLog(
        app_store,
        policy=ConsentPolicy(auto_approve_reversible=True),
    )
    engine = TaskDiscoveryEngine(
        registry,
        LiveMemoryIntentSignals(memory),
        threshold=3,
        policy=ConsentPolicy(auto_approve_reversible=True),
        proposal_sink=proposals.append,
        change_recorder=change_log,
    )

    first = await engine.observe_prompt(
        alice,
        "Please send the weekly status report",
        source_session="chat-1",
    )
    second = await engine.observe_prompt(
        alice,
        "Can you send the weekly status report?",
        source_session="chat-1",
    )
    accepted = await engine.observe_prompt(
        alice,
        "send the weekly status report",
        source_session="chat-1",
    )

    assert first.action == second.action == "below_threshold"
    assert accepted.action == "task_accepted"
    assert accepted.task is not None
    assert len(proposals) == 1
    assert accepted.task.visibility == alice.private_visibility

    task_session = SessionSource(
        platform=Platform.TELEGRAM,
        chat_type="dm",
        chat_id="chat-1",
        internal_user_id=alice.user_id,
        task=accepted.task.id,
    )
    assert f":task:{accepted.task.id}" in build_session_key(task_session)

    in_progress = await registry.transition(
        alice,
        accepted.task.id,
        "in_progress",
        actor="alice",
    )
    completed = await registry.transition(
        alice,
        accepted.task.id,
        "completed",
        actor="alice",
    )
    assert in_progress.status == "in_progress"
    assert completed.status == "completed"
    transitions = await registry.transitions(alice, accepted.task.id)
    assert [(item.from_state, item.to_state, item.actor) for item in transitions] == [
        ("pending", "in_progress", "alice"),
        ("in_progress", "completed", "alice"),
    ]
    assert all(item.ts is not None for item in transitions)

    changes = await change_log.list_changes(alice)
    assert any(
        item.target_kind == "data"
        and item.payload["origin"] == "discovered"
        for item in changes
    )


@pytest.mark.asyncio
async def test_task_negative_access_and_rls(
    postgres_dsn: str,
) -> None:
    _, _, registry = await _fresh_stores(postgres_dsn)
    alice = Principal(user_id="alice", display="Alice", role="member")
    bob = Principal(user_id="bob", display="Bob", role="member")
    owner = Principal(user_id="root", display="Root", role="owner")
    bob_task = await registry.create_task(
        bob,
        spec=TaskSpec(
            title="Bob private",
            description="Only Bob",
            trigger_state="pending",
            completion_state="completed",
            progress_states=("pending", "in_progress", "completed"),
        ),
    )

    assert await registry.get_task(alice, bob_task.id) is None
    assert await registry.get_task(owner, bob_task.id) is not None

    connection = await registry._connect()
    try:
        await connection.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='task_reader')
                THEN CREATE ROLE task_reader NOLOGIN; END IF;
            END $$;
            GRANT USAGE ON SCHEMA app_prod TO task_reader;
            GRANT SELECT ON app_prod.tasks TO task_reader;
            """
        )

        async def visible_titles(user_id: str, role: Role) -> set[str]:
            principal = Principal(user_id=user_id, display=user_id, role=role)
            async with connection.transaction():
                await bind_principal(connection, principal)
                await connection.execute("SET LOCAL ROLE task_reader")
                rows = await connection.fetch(f"SELECT title FROM {TASKS_TABLE}")
                return {str(row["title"]) for row in rows}

        assert await visible_titles("alice", "member") == set()
        assert await visible_titles("bob", "member") == {"Bob private"}
        assert await visible_titles("root", "owner") == {"Bob private"}
    finally:
        await connection.close()
