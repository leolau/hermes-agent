"""Real-path E2E for the FG-05 live pgvector memory tier.

Runs against a throwaway Postgres+pgvector schema (contract C3) so the whole
stack is exercised with real imports: embedding write/query round-trip, semantic
ranking, concurrent ``(user, task)`` writers under Postgres MVCC (no lost writes,
no cross-session bleed), and the **negative-access** guarantee enforced both at
the app layer (``scope_filter``) and — the backstop — by Postgres row-level
security (a ``private:<B>`` row is invisible to member A and visible to the
owner).
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

from hermes_cli.access import Principal, Role, bind_principal, private
from hermes_cli.datastore import StoreMode, get_store
from plugins.memory.supabase_pgvector.store import MEMORY_TABLE, PgvectorMemoryStore

# pgvector/pgvector:pg16 — Postgres 16 with the `vector` extension preinstalled.
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
        pytest.skip("Docker is required for the pgvector E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the pgvector E2E test")

    subprocess.run(["docker", "pull", _PGVECTOR_IMAGE], check=True, capture_output=True)
    container = f"hermes-fg05-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--env", "POSTGRES_PASSWORD=hermes-test",
            "--env", "POSTGRES_DB=hermes_test",
            "--publish", "127.0.0.1::5432",
            _PGVECTOR_IMAGE,
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
            raise RuntimeError("Throwaway pgvector Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False, capture_output=True,
        )


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


def _store(dsn: str, mode: StoreMode = "dev") -> PgvectorMemoryStore:
    return PgvectorMemoryStore(get_store("supabase-app", mode, config=_config(dsn)))


async def _reset(dsn: str) -> None:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS app_dev CASCADE")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_write_query_roundtrip_and_semantic_ranking(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    store = _store(postgres_dsn)
    await store.initialize()
    alice = Principal(user_id="alice", display="Alice", role="member")

    await store.write(alice, "alice enjoys green tea every morning", topic="prefs")
    await store.write(alice, "alice is deploying the billing service on friday")

    results = await store.query(alice, "what does alice like to drink")
    assert results, "expected at least one recalled memory"
    # The beverage memory must outrank the unrelated deployment memory.
    assert "green tea" in results[0].text
    assert results[0].score is not None and results[0].score > 0
    # Round-trip fidelity: stored text + scoping come back intact.
    assert results[0].owner_user_id == "alice"
    assert results[0].visibility == private("alice")


@pytest.mark.asyncio
async def test_negative_access_app_layer(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    store = _store(postgres_dsn)
    await store.initialize()
    alice = Principal(user_id="alice", display="Alice", role="member")
    bob = Principal(user_id="bob", display="Bob", role="member")
    owner = Principal(user_id="root", display="Root", role="owner")

    await store.write(alice, "shared roadmap milestone Q3", visibility="shared")
    await store.write(bob, "bob private launch codeword orchid", visibility="private")

    # Alice cannot recall Bob's private memory...
    alice_hits = await store.query(alice, "launch codeword orchid", top_k=50)
    assert all("orchid" not in r.text for r in alice_hits)
    # ...but she sees the shared row.
    assert any("roadmap milestone" in r.text for r in alice_hits)
    # The owner sees Bob's private row.
    owner_hits = await store.query(owner, "launch codeword orchid", top_k=50)
    assert any("orchid" in r.text for r in owner_hits)


@pytest.mark.asyncio
async def test_negative_access_enforced_by_postgres_rls(postgres_dsn: str) -> None:
    """Backstop: RLS blocks cross-user reads even bypassing the app filter."""
    await _reset(postgres_dsn)
    store = _store(postgres_dsn)
    await store.initialize()
    alice = Principal(user_id="alice", display="Alice", role="member")
    bob = Principal(user_id="bob", display="Bob", role="member")
    owner = Principal(user_id="root", display="Root", role="owner")

    await store.write(owner, "org handbook link", visibility="shared")
    await store.write(alice, "alice private note", visibility="private")
    await store.write(bob, "bob private note", visibility="private")

    conn = await store._connect()
    try:
        await conn.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_reader')
                THEN CREATE ROLE app_reader NOLOGIN; END IF;
            END $$;
            GRANT USAGE ON SCHEMA app_dev TO app_reader;
            GRANT SELECT ON app_dev.memories TO app_reader;
            """
        )

        async def visible_texts(user_id: str, role: Role) -> set[str]:
            principal = Principal(user_id=user_id, display=user_id, role=role)
            async with conn.transaction():
                await bind_principal(conn, principal)
                # Deliberately issue a raw SELECT with NO app-layer scope_filter,
                # under a non-superuser role, so only RLS gates the read.
                await conn.execute("SET LOCAL ROLE app_reader")
                rows = await conn.fetch(f"SELECT text FROM {MEMORY_TABLE}")
                return {r["text"] for r in rows}

        assert await visible_texts("alice", "member") == {
            "org handbook link",
            "alice private note",
        }
        assert await visible_texts("bob", "member") == {
            "org handbook link",
            "bob private note",
        }
        assert await visible_texts("root", "owner") == {
            "org handbook link",
            "alice private note",
            "bob private note",
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_concurrent_sessions_no_lost_writes_no_cross_bleed(
    postgres_dsn: str,
) -> None:
    await _reset(postgres_dsn)
    store = _store(postgres_dsn)
    await store.initialize()

    writers = 6
    per_writer = 12

    async def session(user_index: int) -> None:
        principal = Principal(
            user_id=f"user{user_index}", display=f"u{user_index}", role="member"
        )
        # Each concurrent (user, task) session writes on its OWN connection —
        # Postgres MVCC lets them all commit without a single-writer lock.
        for n in range(per_writer):
            await store.write(
                principal,
                f"user{user_index} task note number {n}",
                topic="concurrent",
            )

    await asyncio.gather(*(session(i) for i in range(writers)))

    owner = Principal(user_id="root", display="Root", role="owner")
    everything = await store.query(
        owner, "task note", top_k=100, topic="concurrent"
    )
    # No lost writes: every concurrent insert is present.
    assert len(everything) == writers * per_writer

    # No cross-session bleed: each member sees exactly its own private rows.
    for i in range(writers):
        member = Principal(user_id=f"user{i}", display="u", role="member")
        rows = await store.query(
            member, "task note", top_k=100, topic="concurrent"
        )
        assert len(rows) == per_writer
        assert all(r.owner_user_id == f"user{i}" for r in rows)


@pytest.mark.asyncio
async def test_initializes_when_vector_extension_lives_in_another_schema(
    postgres_dsn: str,
) -> None:
    """Real-Supabase layout: ``vector`` in ``public`` while app data is in ``app_dev``.

    A standard self-hosted Supabase installs the ``vector`` extension into the
    ``public`` schema, but contract C3 pins each connection's ``search_path`` to
    the app schema (``app_dev``/``app_prod``). The store must still resolve the
    ``vector`` type, its input cast, and the ``<=>`` operator. Regression for the
    crash ``type "vector" does not exist`` on ``store.initialize()`` (and every
    write/query) that only surfaces when the extension is NOT in the app schema —
    which the other tests never hit because ``_connect`` created it inside the
    app schema under the pinned search_path.
    """
    await _reset(postgres_dsn)
    # Force the extension to live ONLY in public, mirroring a real Supabase box.
    conn = await asyncpg.connect(postgres_dsn, ssl=False)
    try:
        await conn.execute("DROP EXTENSION IF EXISTS vector CASCADE")
        await conn.execute("CREATE EXTENSION vector SCHEMA public")
        located = await conn.fetchval(
            "SELECT n.nspname FROM pg_extension e "
            "JOIN pg_namespace n ON n.oid = e.extnamespace "
            "WHERE e.extname = 'vector'"
        )
        assert located == "public"
    finally:
        await conn.close()

    store = _store(postgres_dsn)
    # Before the fix this raises asyncpg: type "vector" does not exist.
    await store.initialize()
    alice = Principal(user_id="alice", display="Alice", role="member")
    await store.write(
        alice, "vector lives in public but semantic recall still works", topic="ext"
    )
    hits = await store.query(alice, "does recall work when pgvector is elsewhere")
    assert hits and "recall still works" in hits[0].text
    # The scoped table itself must still be created in the app schema, not public.
    conn = await asyncpg.connect(postgres_dsn, ssl=False)
    try:
        table_schema = await conn.fetchval(
            "SELECT table_schema FROM information_schema.tables "
            "WHERE table_name = $1 AND table_schema = 'app_dev'",
            MEMORY_TABLE,
        )
        assert table_schema == "app_dev"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_initialize_with_caller_injected_connection_when_vector_elsewhere(
    postgres_dsn: str,
) -> None:
    """A caller-injected connection must still make ``vector`` usable.

    :class:`hermes_cli.goal_management.GoalManagementService.initialize` opens
    one C3 connection (``search_path`` pinned to ``app_dev``) and hands it to
    ``memory.initialize(connection=...)``, ``write``, etc. That bypasses
    ``_connect``'s vector-schema resolution, so on a real Supabase (extension
    in ``public``) the ``hnsw`` index build fails with ``operator class
    "vector_cosine_ops" does not exist`` — and every subsequent write/query
    with the same injected connection would fail too. The store must prepare
    the vector type/opclass/codec on the injected connection as well. Regression
    for that integrated-path crash.
    """
    await _reset(postgres_dsn)
    conn = await asyncpg.connect(postgres_dsn, ssl=False)
    try:
        await conn.execute("DROP EXTENSION IF EXISTS vector CASCADE")
        await conn.execute("CREATE EXTENSION vector SCHEMA public")
    finally:
        await conn.close()

    store = _store(postgres_dsn)
    # Mirror GoalManagementService: a raw store connection pinned to app_dev,
    # NOT the store's own prepared connection, handed in to every call.
    injected = await store._store.connect()
    try:
        # Before the fix: operator class "vector_cosine_ops" does not exist.
        await store.initialize(connection=injected)
        alice = Principal(user_id="alice", display="Alice", role="member")
        written = await store.write(
            alice, "integrated path recall works", topic="ext", connection=injected
        )
        assert written.id
        hits = await store.query(
            alice, "does the injected-connection path recall", connection=injected
        )
        assert hits and "integrated path recall" in hits[0].text
    finally:
        await injected.close()
