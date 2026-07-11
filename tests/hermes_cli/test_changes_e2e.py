"""Postgres E2E for the FG-12 change log (contracts C5 + C6).

Exercises the real path against a throwaway Postgres schema (contract C3):
record data/config/code change events, undo via inverse-op / checkpoint
restore, redo, the ERC-721 **irreversibility** exception (undo refused), and
the **negative-access** test (a ``private:<other>`` change is invisible and
un-undoable to a different member; the owner sees and undoes it).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest

from hermes_cli.access import Principal, private
from hermes_cli.changes import (
    ChangeLog,
    IrreversibleChange,
    NotUndoable,
    code_op,
    config_op,
    data_op,
    initialize_changes,
)
from hermes_cli.consent import ConsentPolicy
from hermes_cli.datastore import get_store


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
    container = f"hermes-fg12-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--env", "POSTGRES_PASSWORD=hermes-test",
            "--env", "POSTGRES_DB=hermes_test",
            "--publish", "127.0.0.1::5432", image,
        ],
        check=True, capture_output=True, text=True,
    )
    try:
        port_result = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            check=True, capture_output=True, text=True,
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
            ["docker", "rm", "--force", container], check=False, capture_output=True
        )


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


async def _reset(dsn: str) -> None:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute(
            "DROP SCHEMA IF EXISTS app_dev CASCADE;"
            "DROP SCHEMA IF EXISTS app_prod CASCADE;"
        )
        await initialize_changes(conn)
    finally:
        await conn.close()


def _owner() -> Principal:
    return Principal(user_id="root", display="Root", role="owner")


def _member(user_id: str) -> Principal:
    return Principal(user_id=user_id, display=user_id, role="member")


async def _seed_notes(dsn: str) -> None:
    """A tiny real data table (in app_prod) that data change-ops mutate."""
    conn = await get_store("supabase-app", "prod", config=_config(dsn)).connect()
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY,
                body TEXT NOT NULL
            )
            """
        )
        await conn.execute("INSERT INTO notes (id, body) VALUES (1, 'original')")
    finally:
        await conn.close()


def _log(dsn: str, *, policy: ConsentPolicy | None = None) -> ChangeLog:
    store = get_store("supabase-app", "prod", config=_config(dsn))
    return ChangeLog(store, policy=policy or ConsentPolicy())


@pytest.mark.asyncio
async def test_data_change_records_and_undo_redo(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    await _seed_notes(postgres_dsn)
    log = _log(postgres_dsn)

    fwd, inv = data_op("notes", {"id": 1},
                       before={"id": 1, "body": "original"},
                       after={"id": 1, "body": "edited"})
    result = await log.record(
        actor_user_id="root", target_kind="data", op=fwd, inverse_op=inv,
        reversible=True, action="edit note 1", target_ref="notes:1",
        payload={"note": 1}, approved=True,
    )

    conn = await get_store("supabase-app", "prod", config=_config(postgres_dsn)).connect()
    try:
        # Recording alone does not mutate the row (op application is the
        # caller's responsibility); apply it, then undo/redo through the log.
        await conn.execute("UPDATE notes SET body = 'edited' WHERE id = 1")

        row = await conn.fetchrow(
            "SELECT actor_user_id, mode, target_kind, visibility, reversible, "
            "payload FROM app_prod.changes WHERE id = $1",
            result.change_ref,
        )
        assert row["actor_user_id"] == "root"
        assert row["mode"] == "prod"
        assert row["target_kind"] == "data"
        assert row["visibility"] == "shared"
        assert row["reversible"] is True

        undo = await log.undo(result.change_ref, _owner())
        assert undo.target_kind == "data"
        body = await conn.fetchval("SELECT body FROM notes WHERE id = 1")
        assert body == "original"

        redo = await log.redo(_owner())
        assert redo.change_ref == result.change_ref
        body = await conn.fetchval("SELECT body FROM notes WHERE id = 1")
        assert body == "edited"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_config_change_round_trips(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _reset(postgres_dsn)
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    from hermes_cli.config import read_raw_config, save_config

    save_config({"agent": {"reasoning_effort": "low"}}, strip_defaults=False)
    log = _log(postgres_dsn)

    fwd, inv = config_op("agent.reasoning_effort", before="low", after="high")
    result = await log.record(
        actor_user_id="root", target_kind="config", op=fwd, inverse_op=inv,
        reversible=True, action="raise reasoning effort",
        target_ref="agent.reasoning_effort", approved=True,
    )
    # Apply the forward op (the change the log recorded).
    from hermes_cli.changes import _apply_config_op

    _apply_config_op(fwd)
    assert read_raw_config()["agent"]["reasoning_effort"] == "high"

    await log.undo(result.change_ref, _owner())
    assert read_raw_config()["agent"]["reasoning_effort"] == "low"

    await log.redo(_owner(), change_ref=result.change_ref)
    assert read_raw_config()["agent"]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_code_change_undo_via_checkpoint(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is required for checkpoint restore")
    await _reset(postgres_dsn)
    monkeypatch.setattr("tools.checkpoint_manager.CHECKPOINT_BASE", tmp_path / "cp")
    from tools.checkpoint_manager import CheckpointManager

    work = tmp_path / "project"
    work.mkdir()
    target = work / "main.py"
    target.write_text("print('v1')\n")
    mgr = CheckpointManager(enabled=True, max_snapshots=50)
    mgr.ensure_checkpoint(str(work), "v1")
    target.write_text("print('v2')\n")
    mgr.new_turn()
    mgr.ensure_checkpoint(str(work), "v2")
    checkpoints = mgr.list_checkpoints(str(work))
    commit_after, commit_before = checkpoints[0]["hash"], checkpoints[1]["hash"]

    fwd, inv = code_op(str(work), commit_before=commit_before,
                       commit_after=commit_after, file_path="main.py")
    log = _log(postgres_dsn)
    result = await log.record(
        actor_user_id="root", target_kind="code", op=fwd, inverse_op=inv,
        reversible=True, action="edit main.py", target_ref="main.py", approved=True,
    )

    await log.undo(result.change_ref, _owner())
    assert target.read_text() == "print('v1')\n"
    await log.redo(_owner())
    assert target.read_text() == "print('v2')\n"


@pytest.mark.asyncio
async def test_irreversible_mint_recorded_and_undo_refused(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    # An explicit approval is mandatory for an irreversible action (D6): a
    # denying callback blocks the record entirely.
    log = _log(postgres_dsn, policy=ConsentPolicy(auto_approve_reversible=True))
    mint_op = {"kind": "data", "table": "tokens", "pk": {"token_id": 7},
               "state": {"token_id": 7, "owner": "root"}}

    with pytest.raises(PermissionError):
        await log.record(
            actor_user_id="root", target_kind="data", op=mint_op, inverse_op=None,
            reversible=False, action="ERC-721 mint", target_ref="token:7",
            approval_callback=lambda *_a, **_k: "deny",
        )

    result = await log.record(
        actor_user_id="root", target_kind="data", op=mint_op, inverse_op=None,
        reversible=False, action="ERC-721 mint", target_ref="token:7",
        approval_callback=lambda *_a, **_k: "once",
    )
    conn = await get_store("supabase-app", "prod", config=_config(postgres_dsn)).connect()
    try:
        row = await conn.fetchrow(
            "SELECT reversible, inverse_op FROM app_prod.changes WHERE id = $1",
            result.change_ref,
        )
        assert row["reversible"] is False
        assert row["inverse_op"] is None
    finally:
        await conn.close()

    with pytest.raises(IrreversibleChange):
        await log.undo(result.change_ref, _owner())


@pytest.mark.asyncio
async def test_negative_access_private_change(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    log = _log(postgres_dsn)

    fwd, inv = data_op("notes", {"id": 1}, before={"id": 1, "body": "a"},
                       after={"id": 1, "body": "b"})
    result = await log.record(
        actor_user_id="bob", target_kind="data", op=fwd, inverse_op=inv,
        reversible=True, action="bob edits note", target_ref="notes:1",
        visibility=private("bob"), approved=True,
    )

    # A different member (alice) cannot see it in the scoped listing…
    alice_view = await log.list_changes(_member("alice"))
    assert all(e.id != result.change_ref for e in alice_view)
    # …nor fetch it…
    with pytest.raises(PermissionError):
        await log.get(result.change_ref, _member("alice"))
    # …nor undo it.
    with pytest.raises(PermissionError):
        await log.undo(result.change_ref, _member("alice"))

    # Bob (the owner of the private row) sees it.
    bob_view = await log.list_changes(_member("bob"))
    assert any(e.id == result.change_ref for e in bob_view)

    # The owner sees and can undo it.
    owner_view = await log.list_changes(_owner())
    assert any(e.id == result.change_ref for e in owner_view)
    await _seed_for_owner_undo(postgres_dsn)
    undo = await log.undo(result.change_ref, _owner())
    assert undo.change_ref == result.change_ref


async def _seed_for_owner_undo(dsn: str) -> None:
    """The private-change undo replays a data inverse-op; make its table exist."""
    conn = await get_store("supabase-app", "prod", config=_config(dsn)).connect()
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        await conn.execute(
            "INSERT INTO notes (id, body) VALUES (1, 'b') "
            "ON CONFLICT (id) DO UPDATE SET body = EXCLUDED.body"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_backup_ref_is_restorable(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A change links a real backup id in ``backup_ref`` and it restores.

    Exercises the real backup engine (``hermes_cli.backup`` quick-snapshot +
    restore) rather than a mock: snapshot config.yaml, record a change carrying
    the snapshot id, clobber the file, then restore from ``backup_ref``.
    """
    await _reset(postgres_dsn)
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    from hermes_cli import backup as backup_mod

    (home / "config.yaml").write_text("agent:\n  reasoning_effort: low\n", encoding="utf-8")
    snap_id = backup_mod.create_quick_snapshot("pre-change", home)
    assert snap_id is not None

    log = _log(postgres_dsn)
    fwd, inv = config_op("agent.reasoning_effort", before="low", after="high")
    result = await log.record(
        actor_user_id="root", target_kind="config", op=fwd, inverse_op=inv,
        reversible=True, action="risky config edit",
        target_ref="agent.reasoning_effort", backup_ref=snap_id, approved=True,
    )

    conn = await get_store("supabase-app", "prod", config=_config(postgres_dsn)).connect()
    try:
        stored = await conn.fetchval(
            "SELECT backup_ref FROM app_prod.changes WHERE id = $1", result.change_ref
        )
        assert stored == snap_id
    finally:
        await conn.close()

    # A destructive edit, then restore from the recorded backup.
    (home / "config.yaml").write_text("agent: {}\n", encoding="utf-8")
    assert backup_mod.restore_quick_snapshot(snap_id, home) is True
    assert "reasoning_effort: low" in (home / "config.yaml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_auto_approval_decision_recorded(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    # Consent granted → reversible change auto-approves with no prompt callback.
    log = _log(postgres_dsn, policy=ConsentPolicy(auto_approve_reversible=True))
    fwd, inv = config_op("x.y", before=1, after=2)

    def deny(*_a, **_k) -> str:
        raise AssertionError("callback must not be invoked when auto-approving")

    result = await log.record(
        actor_user_id="root", target_kind="config", op=fwd, inverse_op=inv,
        reversible=True, action="tweak", target_ref="x.y",
        approval_callback=deny,
    )
    assert result.decision.mode == "auto"
    conn = await get_store("supabase-app", "prod", config=_config(postgres_dsn)).connect()
    try:
        decision = await conn.fetchval(
            "SELECT decision FROM app_prod.approvals WHERE id = $1", result.approval_ref
        )
        assert decision == "auto"
    finally:
        await conn.close()
