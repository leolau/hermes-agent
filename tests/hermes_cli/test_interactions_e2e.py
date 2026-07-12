from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource
from hermes_cli.access import Principal, bind_principal
from hermes_cli.changes import ChangeLog, config_op, initialize_changes
from hermes_cli.config import load_config, save_config
from hermes_cli.datastore import get_store, initialize_supabase_app
from hermes_cli.interactions import (
    Interaction,
    InteractionLedger,
    InteractionTrace,
    bind_trace,
    observe,
)
from hermes_cli.plugins import get_pre_tool_call_block_message
from model_tools import _emit_post_tool_call_hook


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
    container = f"hermes-fg16-{uuid.uuid4().hex[:12]}"
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


def _owner() -> Principal:
    return Principal(user_id="root", display="Root", role="owner")


def _member(user_id: str) -> Principal:
    return Principal(user_id=user_id, display=user_id, role="member")


async def _reset(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    try:
        await connection.execute(
            "DROP SCHEMA IF EXISTS app_dev CASCADE;"
            "DROP SCHEMA IF EXISTS app_prod CASCADE;"
        )
        await initialize_supabase_app(connection)
        await initialize_changes(connection)
    finally:
        await connection.close()


def _trace(trace_id: str, actor: str, *, old: bool = False) -> InteractionTrace:
    trace = InteractionTrace(
        trace_id=trace_id,
        actor_user_id=actor,
        session_key=f"telegram:{actor}",
        platform="telegram",
        mode="prod",
    )
    timestamp = datetime.now(UTC) - timedelta(days=45) if old else None
    trace.emit(
        "inbound",
        ref=f"msg:{actor}",
        summary="Inbound",
        ts=timestamp,
    )
    trace.emit("turn", ref=f"turn:{actor}", summary="Turn", ts=timestamp)
    return trace


def _gateway_runner(monkeypatch: pytest.MonkeyPatch, home) -> gateway_run.GatewayRunner:
    runner = gateway_run.GatewayRunner(GatewayConfig())
    runner.adapters = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    monkeypatch.setattr(runner, "_is_user_authorized", lambda _source: True)
    monkeypatch.setattr(runner, "_set_session_env", lambda _context: None)
    monkeypatch.setattr(
        runner,
        "_handle_active_session_busy_message",
        AsyncMock(return_value=False),
    )
    runner._session_db = MagicMock()
    monkeypatch.setattr(
        runner,
        "_recover_telegram_topic_thread_id",
        lambda _source: None,
    )
    monkeypatch.setattr(runner, "_cache_session_source", lambda _key, _source: None)
    monkeypatch.setattr(
        runner,
        "_is_session_run_current",
        lambda _key, _generation: True,
    )
    monkeypatch.setattr(runner, "_reply_anchor_for_event", lambda _event: None)
    monkeypatch.setattr(runner, "_get_guild_id", lambda _event: None)
    monkeypatch.setattr(
        runner,
        "_should_send_voice_reply",
        lambda *_args, **_kwargs: False,
    )
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:dm:alice",
        session_id="sess-trace-e2e",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store.load_transcript.return_value = []
    monkeypatch.setattr(gateway_run, "_hermes_home", home)
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {"api_key": "fake"},
    )
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100_000,
    )
    return runner


@pytest.mark.asyncio
async def test_gateway_real_path_flushes_one_trace_to_postgres(
    postgres_dsn: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _reset(postgres_dsn)
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    save_config(
        {
            "datastore": {"supabase_app": {"dsn": postgres_dsn}},
            "action_tracking": {
                "enabled": True,
                "retention_days": 30,
                "rollup": True,
                "sample": 1.0,
            },
        },
        strip_defaults=False,
    )
    config = load_config()
    store = get_store("supabase-app", "prod", config=config)
    runner = _gateway_runner(monkeypatch, home)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="alice",
        user_id="alice",
    )
    event = MessageEvent(
        text="trace the real path",
        source=source,
        message_id="msg-e2e",
    )

    async def run_agent(**_kwargs):
        observe("turn", ref="turn-e2e", summary="Turn")
        assert get_pre_tool_call_block_message(
            "terminal",
            {"command": "printf traced"},
            turn_id="turn-e2e",
            tool_call_id="call-e2e",
        ) is None
        _emit_post_tool_call_hook(
            function_name="terminal",
            function_args={"command": "printf traced"},
            result="traced",
            turn_id="turn-e2e",
            tool_call_id="call-e2e",
            status="ok",
        )
        forward, inverse = config_op("agent.reasoning_effort", before="low", after="high")
        await ChangeLog(store, config=config).record(
            actor_user_id="alice",
            target_kind="config",
            op=forward,
            inverse_op=inverse,
            reversible=True,
            action="raise reasoning effort",
            target_ref="agent.reasoning_effort",
            approved=True,
        )
        observe("cost", ref="turn-e2e:api:1", summary="amount_usd=0.01")
        return {
            "final_response": "traced",
            "messages": [
                {"role": "user", "content": "trace the real path"},
                {"role": "assistant", "content": "traced"},
            ],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
            "api_calls": 1,
            "failed": False,
        }

    monkeypatch.setattr(runner, "_run_agent", AsyncMock(side_effect=run_agent))
    response = await runner._handle_message_with_agent(
        event,
        source,
        "agent:main:telegram:dm:alice",
        1,
    )

    assert response == "traced"
    traces = await InteractionLedger(store, config=config).list_traces(_owner())
    assert len(traces) == 1
    events, rollup = await InteractionLedger(store, config=config).get_trace(
        traces[0].trace_id,
        _owner(),
    )
    assert rollup is None
    assert [event.kind for event in events] == [
        "inbound",
        "turn",
        "tool_call",
        "tool_result",
        "approval",
        "change",
        "cost",
        "outbound",
    ]
    assert {event.trace_id for event in events} == {traces[0].trace_id}
    by_kind = {event.kind: event for event in events}
    assert by_kind["turn"].parent_id == by_kind["inbound"].id
    assert by_kind["tool_call"].parent_id == by_kind["turn"].id
    assert by_kind["tool_result"].parent_id == by_kind["tool_call"].id
    assert by_kind["change"].parent_id == by_kind["turn"].id
    assert by_kind["cost"].parent_id == by_kind["turn"].id
    assert by_kind["outbound"].parent_id == by_kind["turn"].id


@pytest.mark.asyncio
async def test_temp_home_real_path_change_link_and_scoped_query(
    postgres_dsn: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _reset(postgres_dsn)
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    save_config(
        {
            "datastore": {"supabase_app": {"dsn": postgres_dsn}},
            "action_tracking": {
                "enabled": True,
                "retention_days": 30,
                "rollup": True,
                "sample": 1.0,
            },
        },
        strip_defaults=False,
    )
    config = load_config()
    store = get_store("supabase-app", "prod", config=config)
    ledger = InteractionLedger(store, config=config)
    alice_trace = _trace("trc_alice", "alice")

    with bind_trace(alice_trace):
        forward, inverse = config_op("agent.reasoning_effort", before="low", after="high")
        result = await ChangeLog(store, config=config).record(
            actor_user_id="alice",
            target_kind="config",
            op=forward,
            inverse_op=inverse,
            reversible=True,
            action="raise reasoning effort",
            target_ref="agent.reasoning_effort",
            approved=True,
        )
        observe("cost", ref="turn:alice:api:1", summary="amount_usd=0.01")

    await ledger.flush(alice_trace)
    await ledger.flush(_trace("trc_bob", "bob"))

    alice_view = await ledger.list_traces(_member("alice"))
    assert [trace.trace_id for trace in alice_view] == ["trc_alice"]
    hidden_events, hidden_rollup = await ledger.get_trace(
        "trc_bob",
        _member("alice"),
    )
    assert hidden_events == []
    assert hidden_rollup is None
    assert {trace.trace_id for trace in await ledger.list_traces(_owner())} == {
        "trc_alice",
        "trc_bob",
    }
    events, rollup = await ledger.get_trace("trc_alice", _member("alice"))
    assert rollup is None
    assert {"inbound", "turn", "approval", "change", "cost"} <= {
        event.kind for event in events
    }
    connection = await store.connect()
    try:
        stored_trace_id = await connection.fetchval(
            "SELECT trace_id FROM changes WHERE id = $1",
            result.change_ref,
        )
        assert stored_trace_id == "trc_alice"
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_postgres_rls_member_negative_access_and_owner_visibility(
    postgres_dsn: str,
) -> None:
    await _reset(postgres_dsn)
    config = {"datastore": {"supabase_app": {"dsn": postgres_dsn}}}
    store = get_store("supabase-app", "prod", config=config)
    ledger = InteractionLedger(store, config=config)
    await ledger.flush(_trace("trc_alice", "alice"))
    await ledger.flush(_trace("trc_bob", "bob"))

    connection = await store.connect()
    try:
        await connection.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname = 'interaction_reader'
                )
                THEN CREATE ROLE interaction_reader NOLOGIN; END IF;
            END $$;
            GRANT USAGE ON SCHEMA app_prod TO interaction_reader;
            GRANT SELECT ON app_prod.interactions TO interaction_reader;
            """
        )

        async def visible_trace_ids(principal: Principal) -> list[str]:
            async with connection.transaction():
                await bind_principal(connection, principal)
                await connection.execute("SET LOCAL ROLE interaction_reader")
                rows = await connection.fetch(
                    "SELECT DISTINCT trace_id FROM interactions ORDER BY trace_id"
                )
                return [row["trace_id"] for row in rows]

        assert await visible_trace_ids(_member("alice")) == ["trc_alice"]
        assert await visible_trace_ids(_member("bob")) == ["trc_bob"]
        assert await visible_trace_ids(_owner()) == ["trc_alice", "trc_bob"]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_retention_rolls_up_then_deletes_expired_rows(
    postgres_dsn: str,
) -> None:
    await _reset(postgres_dsn)
    config = {
        "datastore": {"supabase_app": {"dsn": postgres_dsn}},
        "action_tracking": {
            "enabled": True,
            "retention_days": 30,
            "rollup": True,
            "sample": 1.0,
        },
    }
    store = get_store("supabase-app", "prod", config=config)
    ledger = InteractionLedger(store, config=config)
    await ledger.flush(_trace("trc_old", "alice", old=True))
    await ledger.flush(_trace("trc_new", "alice"))
    mixed = _trace("trc_mixed", "alice", old=True)
    mixed.emit("cost", ref="current-cost", summary="amount_usd=0.01")
    await ledger.flush(mixed)

    maintenance = await ledger.apply_retention()
    assert maintenance == {"rolled_up": 1, "deleted": 2}
    old_events, old_rollup = await ledger.get_trace("trc_old", _member("alice"))
    assert old_events == []
    assert old_rollup is not None
    assert old_rollup.kind_counts == {"inbound": 1, "turn": 1}
    new_events, new_rollup = await ledger.get_trace("trc_new", _member("alice"))
    assert len(new_events) == 2
    assert new_rollup is None
    mixed_events, mixed_rollup = await ledger.get_trace(
        "trc_mixed",
        _member("alice"),
    )
    assert len(mixed_events) == 3
    assert mixed_rollup is None
