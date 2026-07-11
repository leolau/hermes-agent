"""FG-03 x C1 E2E — bind channel identity -> Principal, then key by internal user.

Exercises the real ``resolve_principal`` seam against a throwaway Postgres
schema (no mocks): two channel identities enrol as distinct internal users, and
``bind_channel_principal`` stamps the resolved system ``user_id`` onto the session
source so the C4 key isolates per *internal* user. Includes the mandated
negative-access check: a ``private:<other>`` tier is invisible to a different
member and visible to the owner.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import asyncpg
import pytest

from gateway.config import Platform
from gateway.inbound import bind_channel_principal
from gateway.session import SessionSource, build_session_key
from hermes_cli.access import PrincipalStore, can_read, private
from hermes_cli.datastore import get_store, initialize_supabase_app


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the Postgres E2E test")
    if subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    ).returncode != 0:
        pytest.skip("Docker daemon is unavailable for the Postgres E2E test")

    image = (
        "postgres@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    container = f"hermes-fg03-{uuid.uuid4().hex[:12]}"
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
        port = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            check=True, capture_output=True, text=True,
        ).stdout.strip().rsplit(":", 1)[1]
        dsn = f"postgresql://postgres:hermes-test@127.0.0.1:{port}/hermes_test"
        for _ in range(60):
            try:
                asyncio.run(_probe_postgres(dsn))
                break
            except (OSError, asyncpg.PostgresError):
                time.sleep(0.25)
        else:
            raise RuntimeError("Throwaway Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False, capture_output=True,
        )


def _channel_source(platform: Platform, chat_id: str, user_id: str) -> SessionSource:
    return SessionSource(
        platform=platform, chat_id=chat_id, chat_type="dm", user_id=user_id
    )


@pytest.mark.asyncio
async def test_bind_principal_keys_session_by_internal_user(postgres_dsn: str) -> None:
    config = {"datastore": {"supabase_app": {"dsn": postgres_dsn}}}
    # Create both schemas, then use the prod app store (auth lives in prod).
    setup = await get_store("supabase-app", "prod", config=config).connect()
    try:
        await initialize_supabase_app(setup)
    finally:
        await setup.close()

    store = PrincipalStore(get_store("supabase-app", "prod", config=config))

    owner = await store.enroll("owner_user", display="Owner", role="owner")
    # Two members auto-enrol through the C1 seam (pairing-approved).
    paired = lambda platform, cuid: True  # noqa: E731 - test stub

    src_a = _channel_source(Platform.TELEGRAM, "chatA", "tg_alice")
    src_b = _channel_source(Platform.TELEGRAM, "chatB", "tg_bob")

    p_a = await bind_channel_principal(store=store, source=src_a, is_paired=paired)
    p_b = await bind_channel_principal(store=store, source=src_b, is_paired=paired)

    assert p_a is not None and p_b is not None
    assert p_a.user_id != p_b.user_id
    # bind_channel_principal stamps the resolved internal user onto the source.
    assert src_a.internal_user_id == p_a.user_id
    assert src_b.internal_user_id == p_b.user_id

    key_a = build_session_key(src_a)
    key_b = build_session_key(src_b)
    assert f":usr:{p_a.user_id}" in key_a
    assert key_a != key_b

    # The internal-user dimension isolates even when channel identity is held
    # constant (e.g. a shared inbox): same base source, different resolved user.
    shared = _channel_source(Platform.TELEGRAM, "shared_chat", "")
    ku_a = build_session_key(shared, internal_user_id=p_a.user_id)
    ku_b = build_session_key(shared, internal_user_id=p_b.user_id)
    assert ku_a != ku_b

    # Re-binding the same channel identity is stable (idempotent enrolment).
    src_a2 = _channel_source(Platform.TELEGRAM, "chatA", "tg_alice")
    p_a2 = await bind_channel_principal(store=store, source=src_a2, is_paired=paired)
    assert p_a2 is not None and p_a2.user_id == p_a.user_id

    # Negative access (C2), with real principals: a private:<A> tier is readable
    # by A and by the owner, but NOT by a different member.
    a_private = private(p_a.user_id)
    assert can_read(p_a, a_private) is True
    assert can_read(owner, a_private) is True
    assert can_read(p_b, a_private) is False


@pytest.mark.asyncio
async def test_unpaired_identity_leaves_source_unbound(postgres_dsn: str) -> None:
    config = {"datastore": {"supabase_app": {"dsn": postgres_dsn}}}
    setup = await get_store("supabase-app", "prod", config=config).connect()
    try:
        await initialize_supabase_app(setup)
    finally:
        await setup.close()

    store = PrincipalStore(get_store("supabase-app", "prod", config=config))
    src = _channel_source(Platform.TELEGRAM, "chatX", "tg_stranger")
    principal = await bind_channel_principal(
        store=store, source=src, is_paired=lambda p, u: False
    )
    assert principal is None
    assert src.internal_user_id is None
    # Falls back to channel-identity-only keying (no :usr: dimension).
    assert ":usr:" not in build_session_key(src)
