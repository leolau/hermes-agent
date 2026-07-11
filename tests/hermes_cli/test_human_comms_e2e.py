"""Postgres E2E for FG-10 human-comms notifications (contracts C1/C2/C3/C6).

Exercises the real path against a throwaway Postgres schema (contract C3):
create pending approvals / proactive asks, list them C2-scoped, answer them,
the **cross-surface dedupe** guarantee (answering on Telegram clears the web
item and vice-versa), the C6 auto-approve / quiet-hours / irreversible rules,
and the **negative-access** test (a ``private:<other>`` item is invisible and
un-answerable to a different member; the owner sees and answers it).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import datetime

import asyncpg
import pytest

from hermes_cli.access import Principal, private
from hermes_cli.consent import ConsentPolicy
from hermes_cli.datastore import get_store
from hermes_cli.human_comms import NotificationStore


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
    container = f"hermes-fg10-{uuid.uuid4().hex[:12]}"
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


def _owner() -> Principal:
    return Principal(user_id="root", display="Root", role="owner")


def _member(user_id: str) -> Principal:
    return Principal(user_id=user_id, display=user_id, role="member")


def _store(dsn: str) -> NotificationStore:
    return get_store("supabase-app", "prod", config=_config(dsn))


def _ntf(dsn: str, *, policy: ConsentPolicy | None = None) -> NotificationStore:
    return NotificationStore(_store(dsn), policy=policy or ConsentPolicy())


async def _reset(dsn: str) -> None:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute(
            "DROP SCHEMA IF EXISTS app_dev CASCADE;"
            "DROP SCHEMA IF EXISTS app_prod CASCADE;"
        )
    finally:
        await conn.close()
    await _ntf(dsn).initialize()


@pytest.mark.asyncio
async def test_create_list_and_answer(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    ntf = _ntf(postgres_dsn)

    result = await ntf.create(
        kind="approval",
        target_user_id="alice",
        title="delete file X",
        command="rm X",
        reversible=True,
    )
    assert result.created is True
    assert result.auto_answered is False
    assert result.notification.is_pending

    pending = await ntf.list_pending(_member("alice"))
    assert [n.title for n in pending] == ["delete file X"]

    answer = await ntf.answer(
        result.notification.id, _member("alice"), answer="approved", via="web"
    )
    assert answer.newly_answered is True
    assert answer.notification.status == "answered"
    assert answer.notification.answered_via == "web"

    # Cleared from the pending surface.
    assert await ntf.list_pending(_member("alice")) == []


@pytest.mark.asyncio
async def test_cross_surface_dedupe_answer_once(postgres_dsn: str) -> None:
    """Answering on Telegram clears the web item and vice-versa (idempotent)."""
    await _reset(postgres_dsn)
    ntf = _ntf(postgres_dsn)

    created = await ntf.create(
        kind="approval", target_user_id="alice", title="ship it", reversible=True
    )
    nid = created.notification.id

    # Telegram answers first.
    first = await ntf.answer(nid, _member("alice"), answer="approved", via="telegram")
    assert first.newly_answered is True
    assert first.notification.answered_via == "telegram"

    # Web tries to answer the same item — no-op, returns the settled row.
    second = await ntf.answer(nid, _member("alice"), answer="denied", via="web")
    assert second.newly_answered is False
    # The original answer/surface is preserved; the second answer does not win.
    assert second.notification.answer == "approved"
    assert second.notification.answered_via == "telegram"


@pytest.mark.asyncio
async def test_dedupe_key_collapses_pending_items(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    ntf = _ntf(postgres_dsn)

    first = await ntf.create(
        kind="proactive_ask",
        target_user_id="alice",
        title="lunch?",
        dedupe_key="ask:lunch",
    )
    second = await ntf.create(
        kind="proactive_ask",
        target_user_id="alice",
        title="lunch? (resent)",
        dedupe_key="ask:lunch",
    )
    assert first.created is True
    assert second.created is False
    assert second.notification.id == first.notification.id
    # Only one pending row exists despite two create calls.
    assert len(await ntf.list_pending(_member("alice"))) == 1


@pytest.mark.asyncio
async def test_c6_reversible_auto_approved_on_create(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    ntf = _ntf(postgres_dsn, policy=ConsentPolicy(auto_approve_reversible=True))

    result = await ntf.create(
        kind="approval", target_user_id="alice", title="tidy tmp", reversible=True
    )
    assert result.auto_answered is True
    assert result.notification.status == "answered"
    assert result.notification.answered_via == "auto"
    # Auto-answered => not pending on any surface.
    assert await ntf.list_pending(_member("alice")) == []


@pytest.mark.asyncio
async def test_c6_irreversible_always_pending(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    ntf = _ntf(postgres_dsn, policy=ConsentPolicy(auto_approve_reversible=True))

    result = await ntf.create(
        kind="approval",
        target_user_id="alice",
        title="mint NFT",
        reversible=False,
    )
    # Standing consent can never silence an irreversible approval (D6).
    assert result.auto_answered is False
    assert result.notification.is_pending
    assert len(await ntf.list_pending(_member("alice"))) == 1


@pytest.mark.asyncio
async def test_c6_quiet_hours_defers_proactive_delivery(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    ntf = _ntf(
        postgres_dsn,
        policy=ConsentPolicy(quiet_hours_start=22, quiet_hours_end=7),
    )

    night = datetime(2026, 7, 11, 23, 30)
    held = await ntf.create(
        kind="proactive_ask", target_user_id="alice", title="nudge", now=night
    )
    assert held.deliver_now is False
    assert held.notification.delivered is False

    day = datetime(2026, 7, 11, 12, 0)
    sent = await ntf.create(
        kind="proactive_ask", target_user_id="alice", title="nudge2", now=day
    )
    assert sent.deliver_now is True
    assert sent.notification.delivered is True


@pytest.mark.asyncio
async def test_negative_access_private_item(postgres_dsn: str) -> None:
    """A private:<alice> item is invisible + un-answerable to another member;
    the owner sees and answers it (contract C2)."""
    await _reset(postgres_dsn)
    ntf = _ntf(postgres_dsn)

    created = await ntf.create(
        kind="approval",
        target_user_id="alice",
        title="alice-only decision",
        visibility=private("alice"),
        reversible=True,
    )
    nid = created.notification.id

    # A different member sees nothing and cannot answer.
    assert await ntf.list_pending(_member("mallory")) == []
    with pytest.raises(PermissionError):
        await ntf.answer(nid, _member("mallory"), answer="approved", via="web")

    # Alice (the target) sees it.
    assert [n.id for n in await ntf.list_pending(_member("alice"))] == [nid]

    # The owner sees everything and can answer.
    assert [n.id for n in await ntf.list_pending(_owner())] == [nid]
    answered = await ntf.answer(nid, _owner(), answer="approved", via="telegram")
    assert answered.newly_answered is True
