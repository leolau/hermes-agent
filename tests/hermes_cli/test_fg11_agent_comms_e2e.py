"""Real-path E2E for FG-11 agent-comms MCP against a throwaway Postgres.

Exercises the whole stack with real imports (contract C3 routing, C2 scoping,
C6 gating) — no mocks of the data layer:

* the mode-aware ``mcp_endpoints`` registry: a dev endpoint written in ``dev``
  is invisible from a ``prod`` store (mode isolation), and a member sees shared
  + its own private endpoints but never another member's private one (C2);
* the server-side scoped surface: ``memory_search`` returns only what the
  resolved principal may read (negative-access: member never sees another
  member's private memory; owner sees all), and ``memory_add`` is denied to a
  viewer / when C6 approval is withheld, and succeeds once approved;
* **cache-safety**: registering a new endpoint does not mutate an already-built
  ("live-conversation") toolset — the new endpoint only appears in a *fresh*
  resolution for a future session.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import asyncpg
import pytest

from hermes_cli.access import Principal, PrincipalStore
from hermes_cli.datastore import get_store, initialize_supabase_app
from hermes_cli.mcp_endpoints import MCPEndpointRegistry
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
        pytest.skip("Docker is required for the FG-11 E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the FG-11 E2E test")

    subprocess.run(["docker", "pull", _PGVECTOR_IMAGE], check=True, capture_output=True)
    container = f"hermes-fg11-{uuid.uuid4().hex[:12]}"
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
            raise RuntimeError("Throwaway Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False, capture_output=True,
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
        # Recreate the app_dev/app_prod schemas + base C1/C5 tables (C3), so
        # the C1 PrincipalStore can enrol into a schema that already exists.
        await initialize_supabase_app(conn)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Client-side registry: mode isolation + C2 scoping + cache-safety
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registry_scoping_and_mode_isolation(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    cfg = _config(postgres_dsn)
    owner = Principal(user_id="root", display="root", role="owner")
    alice = Principal(user_id="alice", display="alice", role="member")
    bob = Principal(user_id="bob", display="bob", role="member")

    prod = MCPEndpointRegistry(get_store("supabase-app", "prod", config=cfg))
    dev = MCPEndpointRegistry(get_store("supabase-app", "dev", config=cfg))
    await prod.initialize()
    await dev.initialize()

    # Prod endpoints: one shared, one private to each member.
    await prod.register(
        owner, "shared_prod", "remote",
        {"type": "http", "url": "https://shared/mcp"}, visibility="shared",
    )
    await prod.register(
        alice, "alice_prod", "in_house",
        {"type": "stdio", "command": "npx", "args": ["-y", "a"]},
    )
    await prod.register(
        bob, "bob_prod", "in_house", {"type": "stdio", "command": "bobcmd"},
    )
    # A dev-only endpoint — must never surface in prod (mode isolation, C3).
    await dev.register(
        owner, "dev_only", "remote",
        {"type": "http", "url": "https://dev/mcp"}, visibility="shared",
    )

    # C2: alice sees shared + her own private, NOT bob's private.
    alice_names = {ep.name for ep in await prod.list_for_principal(alice)}
    assert alice_names == {"shared_prod", "alice_prod"}
    bob_names = {ep.name for ep in await prod.list_for_principal(bob)}
    assert bob_names == {"shared_prod", "bob_prod"}
    # Owner bypasses scoping and sees every prod endpoint.
    owner_names = {ep.name for ep in await prod.list_for_principal(owner)}
    assert owner_names == {"shared_prod", "alice_prod", "bob_prod"}

    # Mode isolation: the dev endpoint is only visible from the dev store.
    assert "dev_only" not in owner_names
    assert "dev_only" in {ep.name for ep in await dev.list_for_principal(owner)}

    # Config translation matches the mcp_servers shape the client consumes.
    configs = await prod.resolve_server_configs(alice)
    assert configs["shared_prod"] == {"url": "https://shared/mcp"}
    assert configs["alice_prod"] == {"command": "npx", "args": ["-y", "a"]}


@pytest.mark.asyncio
async def test_registration_does_not_mutate_live_toolset(postgres_dsn: str) -> None:
    """Cache-safety: a new endpoint serves future sessions, not a live one."""
    await _reset(postgres_dsn)
    cfg = _config(postgres_dsn)
    owner = Principal(user_id="root", display="root", role="owner")
    prod = MCPEndpointRegistry(get_store("supabase-app", "prod", config=cfg))
    await prod.initialize()
    await prod.register(
        owner, "first", "remote",
        {"type": "http", "url": "https://first/mcp"}, visibility="shared",
    )

    # A "live conversation" resolves its toolset once at session start.
    live_toolset = await prod.resolve_server_configs(owner)
    assert set(live_toolset) == {"first"}

    # A new endpoint is registered mid-flight.
    await prod.register(
        owner, "second", "remote",
        {"type": "http", "url": "https://second/mcp"}, visibility="shared",
    )

    # The already-resolved live toolset is unchanged (no splicing).
    assert set(live_toolset) == {"first"}
    # Only a fresh resolution (a future session) sees the new endpoint.
    assert set(await prod.resolve_server_configs(owner)) == {"first", "second"}


# ---------------------------------------------------------------------------
# Server-side scoped surface: C2 reads + C6-gated writes
# ---------------------------------------------------------------------------

def _wire_server(monkeypatch, dsn: str):
    """Point the MCP server's store/principal accessors at the throwaway DB."""
    import mcp_serve

    cfg = _config(dsn)
    monkeypatch.setattr(
        mcp_serve, "_get_app_store",
        lambda mode=None: get_store("supabase-app", "dev", config=cfg),
    )
    monkeypatch.setattr(
        mcp_serve, "_get_principal_store",
        lambda: PrincipalStore(get_store("supabase-app", "prod", config=cfg)),
    )
    monkeypatch.setattr(mcp_serve, "_resolve_server_mode", lambda: "dev")

    class _FakeTool:
        def __init__(self, fn):
            self.name = fn.__name__
            self.fn = fn

    class _FakeToolManager:
        def __init__(self):
            self._tools = {}

        def add_tool(self, fn):
            self._tools[fn.__name__] = _FakeTool(fn)

    class _FakeFastMCP:
        def __init__(self, *a, **k):
            self._tool_manager = _FakeToolManager()

        def tool(self):
            def deco(fn):
                self._tool_manager.add_tool(fn)
                return fn
            return deco

    monkeypatch.setattr(mcp_serve, "_MCP_SERVER_AVAILABLE", True)
    monkeypatch.setattr(mcp_serve, "FastMCP", _FakeFastMCP)
    server = mcp_serve.create_mcp_server()
    return mcp_serve, server


async def _seed_principals_and_memories(dsn: str) -> None:
    cfg = _config(dsn)
    principals = PrincipalStore(get_store("supabase-app", "prod", config=cfg))
    await principals.enroll("root", display="Root", role="owner")
    await principals.enroll("alice", display="Alice", role="member")
    await principals.enroll("bob", display="Bob", role="member")
    await principals.link_channel("root", "mcp", "tok-root")
    await principals.link_channel("alice", "mcp", "tok-alice")

    memory = PgvectorMemoryStore(get_store("supabase-app", "dev", config=cfg))
    await memory.initialize()
    root = Principal(user_id="root", display="Root", role="owner")
    alice = Principal(user_id="alice", display="Alice", role="member")
    bob = Principal(user_id="bob", display="Bob", role="member")
    await memory.write(root, "the deploy secret token rotates weekly", visibility="shared")
    await memory.write(alice, "alice private deploy note", topic="prefs")
    await memory.write(bob, "bob private deploy note", topic="prefs")


@pytest.mark.asyncio
async def test_memory_search_is_scoped_to_the_principal(
    postgres_dsn: str, monkeypatch
) -> None:
    await _reset(postgres_dsn)
    await _seed_principals_and_memories(postgres_dsn)
    mcp_serve, server = _wire_server(monkeypatch, postgres_dsn)
    search = server._tool_manager._tools["memory_search"].fn

    def _texts(payload: str) -> list[str]:
        return [r["text"] for r in json.loads(payload)["results"]]

    # Alice (member): sees shared + her own private, never bob's private.
    monkeypatch.setenv(mcp_serve.MCP_PRINCIPAL_ENV, "tok-alice")
    alice_texts = _texts(await search(query="deploy", top_k=10))
    assert any("alice private" in t for t in alice_texts)
    assert all("bob private" not in t for t in alice_texts)

    # Owner: bypasses scoping, sees every row including both privates.
    monkeypatch.setenv(mcp_serve.MCP_PRINCIPAL_ENV, "tok-root")
    root_texts = _texts(await search(query="deploy", top_k=10))
    assert any("alice private" in t for t in root_texts)
    assert any("bob private" in t for t in root_texts)

    # Unauthenticated peer -> anonymous viewer: shared only.
    monkeypatch.delenv(mcp_serve.MCP_PRINCIPAL_ENV, raising=False)
    anon_texts = _texts(await search(query="deploy", top_k=10))
    assert all("private" not in t for t in anon_texts)
    assert any("secret token" in t for t in anon_texts)


@pytest.mark.asyncio
async def test_memory_add_requires_c6_approval(
    postgres_dsn: str, monkeypatch
) -> None:
    await _reset(postgres_dsn)
    await _seed_principals_and_memories(postgres_dsn)
    mcp_serve, server = _wire_server(monkeypatch, postgres_dsn)
    add = server._tool_manager._tools["memory_add"].fn
    monkeypatch.setenv(mcp_serve.MCP_PRINCIPAL_ENV, "tok-alice")

    # Withheld approval (default: no callback -> fails closed) is denied.
    monkeypatch.setattr(mcp_serve, "_get_write_approval_callback", lambda: None)
    denied = json.loads(await add(text="alice new note"))
    assert "not approved" in denied["error"]

    # With an approving C6 callback the write lands and is owned by the caller.
    monkeypatch.setattr(
        mcp_serve, "_get_write_approval_callback",
        lambda: (lambda *_a, **_k: "once"),
    )
    ok = json.loads(await add(text="alice approved note", topic="prefs"))
    assert ok["written"]["owner_user_id"] == "alice"
    assert ok["written"]["visibility"] == "private:alice"


@pytest.mark.asyncio
async def test_viewer_cannot_write_via_server(
    postgres_dsn: str, monkeypatch
) -> None:
    await _reset(postgres_dsn)
    await _seed_principals_and_memories(postgres_dsn)
    mcp_serve, server = _wire_server(monkeypatch, postgres_dsn)
    add = server._tool_manager._tools["memory_add"].fn
    # No credential -> anonymous viewer -> refused even with an approving cb.
    monkeypatch.delenv(mcp_serve.MCP_PRINCIPAL_ENV, raising=False)
    monkeypatch.setattr(
        mcp_serve, "_get_write_approval_callback",
        lambda: (lambda *_a, **_k: "once"),
    )
    result = json.loads(await add(text="should be refused"))
    assert "viewer" in result["error"]
