"""Postgres E2E coverage for approval-gated dev-to-prod promotion."""

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

from hermes_cli.datastore import get_store, initialize_supabase_app
from hermes_cli.promote import promote_artifact


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
    container = f"hermes-fg13-{uuid.uuid4().hex[:12]}"
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


@pytest.mark.asyncio
async def test_promote_definition_records_approval_change_and_promotion(
    postgres_dsn: str,
) -> None:
    config = {"datastore": {"supabase_app": {"dsn": postgres_dsn}}}
    dev_store = get_store("supabase-app", "dev", config=config)
    prod_store = get_store("supabase-app", "prod", config=config)

    connection = await dev_store.connect()
    try:
        await initialize_supabase_app(connection)
        await connection.execute(
            """
            INSERT INTO artifact_definitions (kind, ref, definition)
            VALUES ('config', 'notifications', $1::jsonb)
            """,
            json.dumps({"enabled": True, "channel": "email"}),
        )
        await connection.execute(
            """
            INSERT INTO artifact_definitions (kind, ref, definition)
            VALUES ('config', 'dev-only', $1::jsonb)
            """,
            json.dumps({"enabled": False}),
        )
        await connection.execute(
            "CREATE TABLE application_data (id INTEGER PRIMARY KEY, value TEXT)"
        )
        await connection.execute(
            "INSERT INTO application_data (id, value) VALUES (1, 'never-promote')"
        )
    finally:
        await connection.close()

    approval_requests: list[tuple[str, str]] = []

    def approve(command: str, description: str, **_: object) -> str:
        approval_requests.append((command, description))
        return "once"

    result = await promote_artifact(
        prod_store,
        "config",
        "notifications",
        actor="e2e-operator",
        approval_callback=approve,
    )

    assert approval_requests == [
        (
            "hermes promote config:notifications",
            "promote an authored artifact from dev to prod",
        )
    ]

    prod = await prod_store.connect()
    try:
        assert await prod.fetchval("SELECT current_schema()") == "app_prod"
        promoted = await prod.fetchval(
            """
            SELECT definition
            FROM artifact_definitions
            WHERE kind = 'config' AND ref = 'notifications'
            """
        )
        assert json.loads(promoted) == {"enabled": True, "channel": "email"}
        assert (
            await prod.fetchval(
                """
                SELECT decision FROM approvals WHERE id = $1
                """,
                result.approval_ref,
            )
            == "approved"
        )
        change = await prod.fetchrow(
            """
            SELECT mode, target_kind, approval_ref, op, inverse_op, reversible
            FROM changes
            WHERE id = $1
            """,
            result.change_ref,
        )
        assert change["mode"] == "prod"
        assert change["target_kind"] == "config"
        assert change["approval_ref"] == result.approval_ref
        assert change["reversible"] is True
        assert json.loads(change["op"])[0]["path"].endswith(
            "/config/notifications"
        )
        assert json.loads(change["inverse_op"]) == [
            {
                "op": "remove",
                "path": "/artifact_definitions/config/notifications",
            }
        ]
        promotion = await prod.fetchrow(
            """
            SELECT from_mode, to_mode, approval_ref, change_ref
            FROM promotions
            WHERE id = $1
            """,
            result.promotion_ref,
        )
        assert dict(promotion) == {
            "from_mode": "dev",
            "to_mode": "prod",
            "approval_ref": result.approval_ref,
            "change_ref": result.change_ref,
        }
        assert (
            await prod.fetchval(
                """
                SELECT definition
                FROM artifact_definitions
                WHERE kind = 'config' AND ref = 'dev-only'
                """
            )
            is None
        )
        assert await prod.fetchval(
            "SELECT to_regclass('app_prod.application_data')"
        ) is None
    finally:
        await prod.close()

    dev = await dev_store.connect()
    try:
        source = await dev.fetchval(
            """
            SELECT definition
            FROM artifact_definitions
            WHERE kind = 'config' AND ref = 'notifications'
            """
        )
        assert json.loads(source) == {"enabled": True, "channel": "email"}
        assert (
            await dev.fetchval("SELECT value FROM application_data WHERE id = 1")
            == "never-promote"
        )
    finally:
        await dev.close()


@pytest.mark.asyncio
async def test_denied_promotion_leaves_prod_unchanged(postgres_dsn: str) -> None:
    config = {"datastore": {"supabase_app": {"dsn": postgres_dsn}}}
    dev_store = get_store("supabase-app", "dev", config=config)
    prod_store = get_store("supabase-app", "prod", config=config)

    dev = await dev_store.connect()
    try:
        await initialize_supabase_app(dev)
        await dev.execute(
            """
            INSERT INTO artifact_definitions (kind, ref, definition)
            VALUES ('schema', 'denied-change', $1::jsonb)
            """,
            json.dumps({"sql": "CREATE TABLE denied_change ()"}),
        )
    finally:
        await dev.close()

    with pytest.raises(PermissionError, match="approval was denied"):
        await promote_artifact(
            prod_store,
            "schema",
            "denied-change",
            actor="e2e-operator",
            approval_callback=lambda *_args, **_kwargs: "deny",
        )

    prod = await prod_store.connect()
    try:
        assert (
            await prod.fetchval(
                """
                SELECT definition
                FROM artifact_definitions
                WHERE kind = 'schema' AND ref = 'denied-change'
                """
            )
            is None
        )
        assert (
            await prod.fetchval(
                """
                SELECT COUNT(*)
                FROM approvals
                WHERE target_ref = 'schema:denied-change'
                """
            )
            == 0
        )
    finally:
        await prod.close()


@pytest.mark.asyncio
async def test_schema_promotion_applies_ddl_without_copying_dev_data(
    postgres_dsn: str,
) -> None:
    config = {"datastore": {"supabase_app": {"dsn": postgres_dsn}}}
    dev_store = get_store("supabase-app", "dev", config=config)
    prod_store = get_store("supabase-app", "prod", config=config)
    definition = {
        "sql": (
            "CREATE TABLE promoted_schema_marker "
            "(id INTEGER PRIMARY KEY, payload TEXT)"
        )
    }

    dev = await dev_store.connect()
    try:
        await initialize_supabase_app(dev)
        await dev.execute(definition["sql"])
        await dev.execute(
            "INSERT INTO promoted_schema_marker (id, payload) VALUES (1, 'dev-only')"
        )
        await dev.execute(
            """
            INSERT INTO artifact_definitions (kind, ref, definition)
            VALUES ('schema', 'create-promoted-marker', $1::jsonb)
            """,
            json.dumps(definition),
        )
    finally:
        await dev.close()

    result = await promote_artifact(
        prod_store,
        "schema",
        "create-promoted-marker",
        actor="e2e-operator",
        approval_callback=lambda *_args, **_kwargs: "once",
    )

    prod = await prod_store.connect()
    dev = await dev_store.connect()
    try:
        assert await prod.fetchval("SELECT COUNT(*) FROM promoted_schema_marker") == 0
        assert await dev.fetchval("SELECT COUNT(*) FROM promoted_schema_marker") == 1
        change = await prod.fetchrow(
            """
            SELECT op, inverse_op, reversible
            FROM changes
            WHERE id = $1
            """,
            result.change_ref,
        )
        assert change["reversible"] is False
        assert change["inverse_op"] is None
        assert json.loads(change["op"])[0]["path"] == "/schemas/app_prod"
    finally:
        await prod.close()
        await dev.close()
