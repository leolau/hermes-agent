"""Real-path E2E for the FG-07 tool registry against a throwaway Postgres.

Exercises the whole registry stack with real imports — contract C3 datastore
routing, C1/C2 principals + scoping, C5/C6 approval-gated promotion — with no
mocks of the data layer:

* **registry CRUD + mode isolation (C3):** a tool authored in ``dev`` is
  invisible from a ``prod`` store, and vice-versa;
* **C2 scoping / negative access:** a member sees ``shared`` + its own
  ``private`` tools but never another member's private tool; the owner sees all;
* **write authorization (C1/C2):** a ``viewer`` may not author a tool, and a
  non-owner may not mutate another owner's tool;
* **config contract:** a ``HERMES_*`` config key is rejected;
* **approval-gated dev→prod promotion (C5/C6):** an approved promotion links one
  approval + one change + one promotion row and lands the tool ``disabled`` in
  prod; a denied promotion leaves prod untouched; **no application data is
  copied** (only the definition crosses the boundary);
* **scaffold → registry → FG-11 endpoint wiring:** a scaffolded in-house tool's
  MCP transport is materialized into the FG-11 endpoint registry and referenced
  by the tool row.
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

from hermes_cli.access import Principal
from hermes_cli.datastore import get_store, initialize_supabase_app
from hermes_cli.mcp_endpoints import MCPEndpointRegistry
from hermes_cli.tool_scaffold import scaffold_in_house_tool
from hermes_cli.tools_registry import (
    ToolConfigError,
    ToolRegistry,
    promote_tool,
)

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
        pytest.skip("Docker is required for the FG-07 registry E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the FG-07 registry E2E test")

    subprocess.run(["docker", "pull", _PGVECTOR_IMAGE], check=True, capture_output=True)
    container = f"hermes-fg07-{uuid.uuid4().hex[:12]}"
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
        await initialize_supabase_app(conn)
    finally:
        await conn.close()


_OWNER = Principal(user_id="root", display="Root", role="owner")
_ALICE = Principal(user_id="alice", display="Alice", role="member")
_BOB = Principal(user_id="bob", display="Bob", role="member")
_VIEWER = Principal(user_id="vic", display="Vic", role="viewer")


# ── registry CRUD + mode isolation + C2 scoping ─────────────────────────────

@pytest.mark.asyncio
async def test_registry_crud_scope_and_mode_isolation(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    cfg = _config(postgres_dsn)
    prod = ToolRegistry(get_store("supabase-app", "prod", config=cfg))
    dev = ToolRegistry(get_store("supabase-app", "dev", config=cfg))
    await prod.initialize()
    await dev.initialize()

    # Prod tools: one shared, one private to each member.
    await prod.create(_OWNER, "shared_tool", "in_house", visibility="shared")
    await prod.create(_ALICE, "alice_tool", "in_house")
    await prod.create(_BOB, "bob_tool", "in_house")
    # A dev-only tool must never surface in prod (mode isolation, C3).
    await dev.create(_OWNER, "dev_only", "in_house", visibility="shared")

    # in_house default stack is applied.
    shared = await prod.get("shared_tool")
    assert shared is not None and shared.stack == "nextjs-node"
    assert shared.mode == "prod" and shared.status == "disabled"

    # C2: alice sees shared + her own private, NOT bob's private.
    alice_names = {t.name for t in await prod.list_for_principal(_ALICE)}
    assert alice_names == {"shared_tool", "alice_tool"}
    bob_names = {t.name for t in await prod.list_for_principal(_BOB)}
    assert bob_names == {"shared_tool", "bob_tool"}
    # Owner bypasses scoping and sees every prod tool.
    owner_names = {t.name for t in await prod.list_for_principal(_OWNER)}
    assert owner_names == {"shared_tool", "alice_tool", "bob_tool"}

    # Mode isolation: the dev-only tool is only visible from the dev store.
    assert "dev_only" not in owner_names
    assert "dev_only" in {t.name for t in await dev.list_for_principal(_OWNER)}

    # enable / configure round-trip.
    enabled = await prod.set_enabled(_ALICE, "alice_tool", True)
    assert enabled.enabled is True
    configured = await prod.set_config(_ALICE, "alice_tool", {"threshold": 5})
    assert configured.config_json == {"threshold": 5}

    # delete removes it.
    await prod.delete(_ALICE, "alice_tool")
    assert await prod.get("alice_tool") is None


# ── negative access + write authorization ───────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_author_and_nonowner_cannot_mutate(
    postgres_dsn: str,
) -> None:
    await _reset(postgres_dsn)
    cfg = _config(postgres_dsn)
    prod = ToolRegistry(get_store("supabase-app", "prod", config=cfg))
    await prod.initialize()

    # A viewer may not author a tool.
    with pytest.raises(PermissionError):
        await prod.create(_VIEWER, "viewer_tool", "in_house")

    # A member may not create a tool private to another user.
    with pytest.raises(PermissionError):
        await prod.create(
            _ALICE, "spoof", "in_house", visibility=_BOB.private_visibility
        )

    # bob authors a private tool; alice cannot mutate it, but the owner can.
    await prod.create(_BOB, "bob_secret", "in_house")
    with pytest.raises(PermissionError):
        await prod.set_enabled(_ALICE, "bob_secret", True)
    # Negative-access: alice cannot even see bob's private tool.
    assert "bob_secret" not in {t.name for t in await prod.list_for_principal(_ALICE)}
    # Owner bypass: owner sees + mutates it.
    assert "bob_secret" in {t.name for t in await prod.list_for_principal(_OWNER)}
    owner_toggle = await prod.set_enabled(_OWNER, "bob_secret", True)
    assert owner_toggle.enabled is True


@pytest.mark.asyncio
async def test_config_rejects_hermes_env_key(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    cfg = _config(postgres_dsn)
    prod = ToolRegistry(get_store("supabase-app", "prod", config=cfg))
    await prod.initialize()
    await prod.create(_OWNER, "cfgtool", "in_house", visibility="shared")
    with pytest.raises(ToolConfigError):
        await prod.set_config(_OWNER, "cfgtool", {"HERMES_SECRET": "nope"})


# ── scaffold → registry → FG-11 endpoint wiring ─────────────────────────────

@pytest.mark.asyncio
async def test_scaffold_registers_tool_and_mcp_endpoint(
    postgres_dsn: str, tmp_path: Path
) -> None:
    await _reset(postgres_dsn)
    cfg = _config(postgres_dsn)
    dev_store = get_store("supabase-app", "dev", config=cfg)
    registry = ToolRegistry(dev_store)
    endpoints = MCPEndpointRegistry(dev_store)
    await registry.initialize()
    await endpoints.initialize()

    scaffold = scaffold_in_house_tool("reporter", tmp_path, port=4390)
    endpoint = await endpoints.register(
        _OWNER, "reporter", "in_house", scaffold.mcp_transport(), visibility="shared"
    )
    tool = await registry.create(
        _OWNER,
        "reporter",
        "in_house",
        visibility="shared",
        mcp_endpoint_ref=endpoint.name,
        web_url=scaffold.web_url,
    )

    assert tool.mcp_endpoint_ref == "reporter"
    assert tool.web_url == "http://127.0.0.1:4390"
    # The endpoint is resolvable for a *future* session (cache-safe wiring).
    resolved = await endpoints.resolve_server_configs(_OWNER)
    assert "reporter" in resolved


# ── approval-gated dev→prod promotion (C5 + C6) ─────────────────────────────

async def _audit_counts(dsn: str) -> tuple[int, int, int]:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        approvals = await conn.fetchval("SELECT COUNT(*) FROM app_prod.approvals")
        changes = await conn.fetchval("SELECT COUNT(*) FROM app_prod.changes")
        promotions = await conn.fetchval("SELECT COUNT(*) FROM app_prod.promotions")
        return approvals, changes, promotions
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_promotion_is_approval_gated_and_audited(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    cfg = _config(postgres_dsn)
    dev = ToolRegistry(get_store("supabase-app", "dev", config=cfg))
    prod = ToolRegistry(get_store("supabase-app", "prod", config=cfg))
    await dev.initialize()
    await prod.initialize()

    await dev.create(
        _OWNER, "billing", "in_house", visibility="shared", status="enabled",
        web_url="http://127.0.0.1:4300",
    )
    await dev.set_config(_OWNER, "billing", {"currency": "USD"})

    # Denied promotion: prod untouched, audit rows unchanged.
    before = await _audit_counts(postgres_dsn)
    with pytest.raises(PermissionError):
        await promote_tool(
            get_store("supabase-app", "prod", config=cfg),
            "billing",
            actor="root",
            approval_callback=lambda *_a, **_k: "deny",
        )
    assert await prod.get("billing") is None
    assert await _audit_counts(postgres_dsn) == before

    # Approved promotion: exactly one approval + change + promotion row.
    result = await promote_tool(
        get_store("supabase-app", "prod", config=cfg),
        "billing",
        actor="root",
        approved=True,
    )
    assert result.tool_name == "billing"
    assert result.approval_ref and result.change_ref and result.promotion_ref
    after = await _audit_counts(postgres_dsn)
    assert after == (before[0] + 1, before[1] + 1, before[2] + 1)

    # The promoted tool exists in prod, lands DISABLED, and carries the def
    # (config crossed the boundary) — but dev application data is NOT copied.
    promoted = await prod.get("billing")
    assert promoted is not None
    assert promoted.mode == "prod"
    assert promoted.status == "disabled"
    assert promoted.config_json == {"currency": "USD"}
    assert promoted.web_url == "http://127.0.0.1:4300"

    # The promotion row links the same approval + change references.
    conn = await asyncpg.connect(postgres_dsn, ssl=False)
    try:
        row = await conn.fetchrow(
            "SELECT artifact_kind, from_mode, to_mode, approval_ref, change_ref "
            "FROM app_prod.promotions WHERE id = $1",
            result.promotion_ref,
        )
    finally:
        await conn.close()
    assert row["artifact_kind"] == "tool"
    assert row["from_mode"] == "dev" and row["to_mode"] == "prod"
    assert row["approval_ref"] == result.approval_ref
    assert row["change_ref"] == result.change_ref
