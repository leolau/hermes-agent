from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from hermes_cli.plugins import get_pre_tool_call_block_message
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource
from hermes_cli.interactions import (
    InteractionTrace,
    current_trace_id,
    observe,
)
from model_tools import _emit_post_tool_call_hook


SESSION_KEY = "agent:main:telegram:group:-1001:12345"


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        user_id="12345",
    )


def _event() -> MessageEvent:
    return MessageEvent(
        text="trace this",
        source=_source(),
        message_id="msg-42",
    )


def _runner(monkeypatch, tmp_path):
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
    monkeypatch.setattr(
        runner,
        "_cache_session_source",
        lambda _key, _source: None,
    )
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
        session_key=SESSION_KEY,
        session_id="sess-trace",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="group",
    )
    runner.session_store.load_transcript.return_value = []
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
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
async def test_gateway_mints_one_trace_and_propagates_it_through_turn(
    monkeypatch,
    tmp_path,
) -> None:
    runner = _runner(monkeypatch, tmp_path)
    minted: list[InteractionTrace] = []
    flushed: list[tuple[str, ...]] = []

    class Ledger:
        async def flush(self, trace: InteractionTrace) -> None:
            flushed.append(tuple(event.kind for event in trace.events))

    def create_trace(**kwargs):
        trace = InteractionTrace(
            trace_id="trc_gateway",
            actor_user_id=kwargs["actor_user_id"],
            session_key=kwargs["session_key"],
            platform=kwargs["platform"],
            mode="prod",
        )
        minted.append(trace)
        return trace, Ledger()

    monkeypatch.setattr(
        "hermes_cli.interactions.create_gateway_trace",
        create_trace,
    )

    async def run_agent(**_kwargs):
        assert current_trace_id() == "trc_gateway"
        observe("turn", ref="turn_1", summary="Turn")
        assert get_pre_tool_call_block_message(
            "terminal",
            {"command": "printf traced"},
            task_id="task_1",
            turn_id="turn_1",
            tool_call_id="call_1",
        ) is None
        _emit_post_tool_call_hook(
            function_name="terminal",
            function_args={"command": "printf traced"},
            result="traced",
            task_id="task_1",
            turn_id="turn_1",
            tool_call_id="call_1",
            status="ok",
        )
        observe("cost", ref="turn_1:api:1", summary="amount_usd=0.01")
        return {
            "final_response": "traced",
            "messages": [
                {"role": "user", "content": "trace this"},
                {"role": "assistant", "content": "traced"},
            ],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
            "api_calls": 1,
            "failed": False,
        }

    runner._run_agent = AsyncMock(side_effect=run_agent)
    response = await runner._handle_message_with_agent(
        _event(),
        _source(),
        SESSION_KEY,
        1,
    )

    assert response == "traced"
    assert len(minted) == 1
    assert minted[0].actor_user_id == "12345"
    assert minted[0].session_key == SESSION_KEY
    assert minted[0].platform == "telegram"
    assert flushed == [
        ("inbound", "turn", "tool_call", "tool_result", "cost", "outbound")
    ]
    assert current_trace_id() is None


@pytest.mark.asyncio
async def test_gateway_trace_flush_failure_is_fail_open(
    monkeypatch,
    tmp_path,
) -> None:
    runner = _runner(monkeypatch, tmp_path)
    trace = InteractionTrace(
        trace_id="trc_fail_open",
        actor_user_id="12345",
        session_key=SESSION_KEY,
        platform="telegram",
        mode="prod",
    )

    class FailingLedger:
        async def flush(self, _trace: InteractionTrace) -> None:
            raise RuntimeError("datastore unavailable")

    monkeypatch.setattr(
        "hermes_cli.interactions.create_gateway_trace",
        lambda **_kwargs: (trace, FailingLedger()),
    )
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "still delivered",
            "messages": [
                {"role": "user", "content": "trace this"},
                {"role": "assistant", "content": "still delivered"},
            ],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
            "api_calls": 1,
            "failed": False,
        }
    )

    response = await runner._handle_message_with_agent(
        _event(),
        _source(),
        SESSION_KEY,
        1,
    )

    assert response == "still delivered"
    assert current_trace_id() is None
