"""Postgres E2E + RLS coverage for the FG-01 multi-user access model.

Exercises the real path against a throwaway Postgres schema (contract C3):
principal enrolment, channel resolution / pairing auto-enrol, the
exactly-one-owner invariant, approval-gated ownership transfer with a C5
change-event, and — critically — the **negative access test** enforced by
Postgres row-level security (a ``private:<other>`` row is invisible to a
different member and visible to the owner).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import asyncpg
import pytest

from hermes_cli.access import (
    Principal,
    PrincipalStore,
    Role,
    apply_scope_rls,
    bind_principal,
    ensure_app_role,
    private,
    resolve_principal,
)
from hermes_cli.datastore import get_store, initialize_supabase_app


class _Source:
    """Minimal ChannelOrigin stand-in for resolve_principal tests."""

    def __init__(self, platform: str, user_id: str | None, user_name: str = "") -> None:
        self.platform = platform
        self.user_id = user_id
        self.user_name = user_name


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the Postgres E2E test")
    daemon = subprocess.run(
        ["docker", "info"],
        check=False,
        capture_output=True,
        text=True,
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the Postgres E2E test")

    image = (
        "postgres@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    container = f"hermes-fg01-{uuid.uuid4().hex[:12]}"
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


async def _reset(dsn: str) -> None:
    """Drop and recreate the app schemas so each test starts clean."""
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute(
            "DROP SCHEMA IF EXISTS app_dev CASCADE;"
            "DROP SCHEMA IF EXISTS app_prod CASCADE;"
        )
        await initialize_supabase_app(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_enroll_link_and_resolve_by_channel(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    store = PrincipalStore(get_store("supabase-app", "prod", config=_config(postgres_dsn)))

    alice = await store.enroll("alice", display="Alice")
    assert alice.role == "member"
    assert alice.channels == ()

    await store.link_channel("alice", "telegram", "tg-123")
    resolved = await store.resolve_by_channel("telegram", "tg-123")
    assert resolved is not None
    assert resolved.user_id == "alice"
    assert resolved.channels == ("telegram:tg-123",)

    # Unknown identity resolves to nothing.
    assert await store.resolve_by_channel("telegram", "nobody") is None


@pytest.mark.asyncio
async def test_resolve_principal_auto_enrolls_only_paired_users(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    store = PrincipalStore(get_store("supabase-app", "prod", config=_config(postgres_dsn)))

    unpaired = _Source("discord", "d-1", "Nobody")
    assert (
        await resolve_principal(unpaired, store=store, is_paired=lambda *_: False)
        is None
    )

    paired = _Source("discord", "d-2", "Newbie")
    principal = await resolve_principal(paired, store=store, is_paired=lambda *_: True)
    assert principal is not None
    assert principal.role == "member"
    assert principal.channels == ("discord:d-2",)

    # A second resolve of the same identity returns the existing principal
    # without needing pairing again.
    again = await resolve_principal(paired, store=store, is_paired=lambda *_: False)
    assert again is not None and again.user_id == principal.user_id


@pytest.mark.asyncio
async def test_exactly_one_owner_invariant(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    store = PrincipalStore(get_store("supabase-app", "prod", config=_config(postgres_dsn)))

    await store.enroll("root", display="Root", role="owner")
    owner = await store.get_owner()
    assert owner is not None and owner.user_id == "root"

    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await store.enroll("usurper", display="Usurper", role="owner")


@pytest.mark.asyncio
async def test_transfer_owner_is_atomic_and_recorded(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    config = _config(postgres_dsn)
    store = PrincipalStore(get_store("supabase-app", "prod", config=config))

    await store.enroll("root", display="Root", role="owner")
    await store.enroll("bob", display="Bob", role="member")

    approvals: list[tuple[str, str]] = []

    def approve(command: str, description: str, **_: object) -> str:
        approvals.append((command, description))
        return "once"

    result = await store.transfer_owner(
        "bob", actor="root", approval_callback=approve
    )
    assert approvals and approvals[0][0] == "hermes owner transfer bob"
    assert result.from_user_id == "root"
    assert result.to_user_id == "bob"

    new_owner = await store.get_owner()
    assert new_owner is not None and new_owner.user_id == "bob"
    old = await store.get("root")
    assert old is not None and old.role == "admin"

    # Exactly one owner still holds.
    prod = await get_store("supabase-app", "prod", config=config).connect()
    try:
        owner_count = await prod.fetchval(
            "SELECT COUNT(*) FROM principals WHERE role = 'owner'"
        )
        assert owner_count == 1
        change = await prod.fetchrow(
            """
            SELECT actor, mode, target_kind, op, approval_ref, reversible
            FROM app_prod.changes WHERE id = $1
            """,
            result.change_ref,
        )
        assert change["mode"] == "prod"
        assert change["target_kind"] == "data"
        assert change["approval_ref"] == result.approval_ref
        assert change["reversible"] is True
        op = json.loads(change["op"])
        assert op[0]["op"] == "transfer_owner"
        assert op[0]["from"] == "root" and op[0]["to"] == "bob"
        decision = await prod.fetchval(
            "SELECT decision FROM app_prod.approvals WHERE id = $1",
            result.approval_ref,
        )
        assert decision == "approved"
    finally:
        await prod.close()


@pytest.mark.asyncio
async def test_transfer_denied_leaves_owner_unchanged(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    store = PrincipalStore(get_store("supabase-app", "prod", config=_config(postgres_dsn)))
    await store.enroll("root", display="Root", role="owner")
    await store.enroll("bob", display="Bob", role="member")

    with pytest.raises(PermissionError, match="approval was denied"):
        await store.transfer_owner(
            "bob", actor="root", approval_callback=lambda *_a, **_k: "deny"
        )

    owner = await store.get_owner()
    assert owner is not None and owner.user_id == "root"
    bob = await store.get("bob")
    assert bob is not None and bob.role == "member"


@pytest.mark.asyncio
async def test_transfer_to_unknown_or_dev_is_rejected(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    config = _config(postgres_dsn)
    prod_store = PrincipalStore(get_store("supabase-app", "prod", config=config))
    await prod_store.enroll("root", display="Root", role="owner")

    with pytest.raises(KeyError):
        await prod_store.transfer_owner("ghost", actor="root", approved=True)

    dev_store = PrincipalStore(get_store("supabase-app", "dev", config=config))
    with pytest.raises(ValueError, match="prod app store"):
        await dev_store.transfer_owner("root", actor="root", approved=True)


@pytest.mark.asyncio
async def test_rls_enforces_private_scope_at_the_database(postgres_dsn: str) -> None:
    """Negative access test enforced by Postgres RLS, not just the app layer.

    A ``private:bob`` row is invisible to member ``alice`` and visible to the
    owner; ``shared`` is visible to all. Runs under a non-superuser role so RLS
    is actually applied (superusers bypass RLS).
    """
    await _reset(postgres_dsn)
    store = get_store("supabase-app", "dev", config=_config(postgres_dsn))
    conn = await store.connect()
    try:
        await conn.execute(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                visibility TEXT NOT NULL,
                body TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO memories (id, owner_user_id, visibility, body) VALUES
                (1, 'root', 'shared', 'org note'),
                (2, 'alice', $1, 'alice secret'),
                (3, 'bob', $2, 'bob secret')
            """,
            private("alice"),
            private("bob"),
        )
        await apply_scope_rls(conn, "memories")

        # A non-superuser role so FORCE RLS is actually enforced.
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

        async def visible_ids(user_id: str, role: Role) -> list[int]:
            principal = Principal(user_id=user_id, display=user_id, role=role)
            async with conn.transaction():
                await bind_principal(conn, principal)
                await conn.execute("SET LOCAL ROLE app_reader")
                rows = await conn.fetch("SELECT id FROM memories ORDER BY id")
                return [r["id"] for r in rows]

        # Alice (member) sees shared + her own private, NOT bob's private.
        assert await visible_ids("alice", "member") == [1, 2]
        # Bob (member) sees shared + his own private, NOT alice's.
        assert await visible_ids("bob", "member") == [1, 3]
        # Owner bypasses RLS scope and sees everything.
        assert await visible_ids("root", "owner") == [1, 2, 3]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_link_and_resolve_alias(postgres_dsn: str) -> None:
    """A login subject maps to an existing principal; unknown → None."""
    await _reset(postgres_dsn)
    store = PrincipalStore(
        get_store("supabase-app", "prod", config=_config(postgres_dsn))
    )
    await store.enroll("leo_owner", display="Leo", role="owner")

    subject = "a1b2c3d4-0000-4000-8000-000000000001"
    assert await store.resolve_alias(subject) is None  # no alias yet

    await store.link_alias(subject, "leo_owner")
    assert await store.resolve_alias(subject) == "leo_owner"

    # Re-linking the same subject repoints it (idempotent upsert).
    await store.enroll("carol", display="Carol", role="member")
    await store.link_alias(subject, "carol")
    assert await store.resolve_alias(subject) == "carol"

    # An unknown subject still resolves to nothing.
    assert await store.resolve_alias("no-such-subject") is None
    # Aliasing to a non-existent principal is rejected by the FK.
    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
        await store.link_alias("x", "ghost")


@pytest.mark.asyncio
async def test_app_role_is_least_privilege_and_enforces_rls(
    postgres_dsn: str,
) -> None:
    """``ensure_app_role`` yields a NOBYPASSRLS role that RLS actually binds.

    Proves item (a): a request that runs under ``hermes_app`` (after binding a
    principal) has C2 visibility enforced by Postgres — a member cannot read
    another member's ``private:`` rows even at the database boundary, and the
    role genuinely cannot bypass RLS.
    """
    await _reset(postgres_dsn)
    store = get_store("supabase-app", "dev", config=_config(postgres_dsn))
    conn = await store.connect()
    try:
        await conn.execute(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                visibility TEXT NOT NULL,
                body TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO memories (id, owner_user_id, visibility, body) VALUES
                (1, 'root', 'shared', 'org note'),
                (2, 'alice', $1, 'alice secret'),
                (3, 'bob', $2, 'bob secret')
            """,
            private("alice"),
            private("bob"),
        )
        await apply_scope_rls(conn, "memories")

        await ensure_app_role(conn, store.schema)

        # The role must be non-BYPASSRLS / non-superuser — else RLS is inert.
        flags = await conn.fetchrow(
            "SELECT rolbypassrls, rolsuper, rolcanlogin "
            "FROM pg_roles WHERE rolname = 'hermes_app'"
        )
        assert flags["rolbypassrls"] is False
        assert flags["rolsuper"] is False
        assert flags["rolcanlogin"] is False

        async def visible_ids(user_id: str, role: Role) -> list[int]:
            principal = Principal(user_id=user_id, display=user_id, role=role)
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE hermes_app")
                await bind_principal(conn, principal)
                rows = await conn.fetch("SELECT id FROM memories ORDER BY id")
                return [r["id"] for r in rows]

        assert await visible_ids("alice", "member") == [1, 2]
        assert await visible_ids("bob", "member") == [1, 3]
        assert await visible_ids("root", "owner") == [1, 2, 3]
    finally:
        await conn.close()


class _FakeQuery:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._values.get(key, default)


class _FakeState:
    pass


class _FakeSession:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


class _FakeRequest:
    """Minimal Starlette-Request stand-in for _comms_resolve_principal."""

    def __init__(
        self,
        *,
        subject: str | None = None,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.state = _FakeState()
        if subject is not None:
            self.state.session = _FakeSession(subject)
        self.query_params = _FakeQuery(query or {})
        self.headers = _FakeQuery(headers or {})


@pytest.mark.asyncio
async def test_comms_resolve_principal_binds_logged_in_identity(
    postgres_dsn: str, monkeypatch
) -> None:
    """The web resolver returns the *logged-in* principal, not always owner."""
    from hermes_cli import web_server as ws

    await _reset(postgres_dsn)
    store = get_store("supabase-app", "prod", config=_config(postgres_dsn))
    principals = PrincipalStore(store)
    await principals.enroll("leo_owner", display="Leo", role="owner")
    await principals.enroll("bob", display="Bob", role="member")
    await principals.enroll("adm", display="Adm", role="admin")
    owner_subject = "sub-owner-uuid"
    await principals.link_alias(owner_subject, "leo_owner")

    monkeypatch.setattr(ws, "_comms_app_store", lambda: store)

    # Owner logs in via their aliased subject → resolves to the owner.
    owner = await ws._comms_resolve_principal(
        _FakeRequest(subject=owner_subject), allow_as=True
    )
    assert owner.user_id == "leo_owner" and owner.is_owner

    # A member logs in with their subject-as-user_id → resolves to themselves,
    # for reads AND writes (allow_as=False path).
    member = await ws._comms_resolve_principal(
        _FakeRequest(subject="bob"), allow_as=False
    )
    assert member.user_id == "bob" and member.role == "member"

    # A member's ?as= is ignored — they only ever see themselves.
    still_bob = await ws._comms_resolve_principal(
        _FakeRequest(subject="bob", query={"as": "leo_owner"}), allow_as=True
    )
    assert still_bob.user_id == "bob"

    # Owner/admin ?as= narrows the read view to the requested principal.
    narrowed = await ws._comms_resolve_principal(
        _FakeRequest(subject=owner_subject, query={"as": "bob"}), allow_as=True
    )
    assert narrowed.user_id == "bob"
    admin_narrowed = await ws._comms_resolve_principal(
        _FakeRequest(subject="adm", query={"as": "bob"}), allow_as=True
    )
    assert admin_narrowed.user_id == "bob"

    # An authenticated subject with no enrolled principal fails closed (409).
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await ws._comms_resolve_principal(
            _FakeRequest(subject="unknown-subject"), allow_as=False
        )
    assert excinfo.value.status_code == 409

    # No interactive session → owner-operator fallback.
    fallback = await ws._comms_resolve_principal(
        _FakeRequest(subject=None), allow_as=True
    )
    assert fallback.user_id == "leo_owner"
