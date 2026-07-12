from __future__ import annotations

from unittest.mock import MagicMock

from agent.conversation_loop import _restore_or_build_system_prompt
from hermes_cli import plugins
from hermes_cli.interactions import (
    INTERACTION_KINDS,
    ActionTrackingConfig,
    InteractionTrace,
    bind_trace,
    current_trace_id,
    observe,
    observe_tool_call,
    observe_tool_result,
)
from hermes_cli.plugins import get_pre_tool_call_block_message
from model_tools import _emit_post_tool_call_hook
from tools.approval import (
    reset_current_observability_context,
    set_current_observability_context,
)


def _trace(*, sample: float = 1.0, trace_id: str = "trc_test") -> InteractionTrace:
    return InteractionTrace(
        trace_id=trace_id,
        actor_user_id="usr_alice",
        session_key="telegram:alice:dm",
        platform="telegram",
        mode="prod",
        sample=sample,
    )


def test_interaction_trace_builds_causation_tree_for_every_kind() -> None:
    trace = _trace()
    with bind_trace(trace):
        inbound_id = observe("inbound", ref="msg_1", summary="Inbound")
        turn_id = observe("turn", ref="turn_1", summary="Turn")
        tool_call_id = observe_tool_call(
            tool_call_id="call_1",
            tool_name="terminal",
            turn_id="turn_1",
        )
        tokens = set_current_observability_context(
            turn_id="turn_1",
            tool_call_id="call_1",
        )
        try:
            observe("approval", ref="apr_1", summary="Approved")
            observe("change", ref="chg_1", summary="Changed")
        finally:
            reset_current_observability_context(tokens)
        observe_tool_result(
            tool_call_id="call_1",
            tool_name="terminal",
            status="ok",
            turn_id="turn_1",
        )
        observe("cost", ref="turn_1:api:1", summary="amount_usd=0.01")
        observe("error", ref="turn_1:api:1", summary="timeout")
        observe("core_denied", ref="core:SYSTEM.md", summary="Denied")
        observe("outbound", ref="msg_2", summary="Outbound")

    assert current_trace_id() is None
    assert {event.kind for event in trace.events} == set(INTERACTION_KINDS)
    events = {(event.kind, event.ref): event for event in trace.events}
    assert events[("inbound", "msg_1")].parent_id is None
    assert events[("turn", "turn_1")].parent_id == inbound_id
    assert events[("tool_call", "call_1")].parent_id == turn_id
    assert events[("tool_result", "call_1")].parent_id == tool_call_id
    assert events[("approval", "apr_1")].parent_id == tool_call_id
    assert events[("change", "chg_1")].parent_id == tool_call_id
    assert events[("cost", "turn_1:api:1")].parent_id == turn_id
    assert events[("outbound", "msg_2")].parent_id == turn_id
    assert {event.trace_id for event in trace.events} == {"trc_test"}


def test_tool_sampling_is_deterministic_and_keeps_pairs_together() -> None:
    first = _trace(sample=0.35, trace_id="trc_sampling")
    second = _trace(sample=0.35, trace_id="trc_sampling")
    for trace in (first, second):
        with bind_trace(trace):
            observe("inbound", ref="msg", summary="Inbound")
            observe("turn", ref="turn", summary="Turn")
            for index in range(50):
                ref = f"call_{index}"
                observe_tool_call(
                    tool_call_id=ref,
                    tool_name="terminal",
                    turn_id="turn",
                )
                observe_tool_result(
                    tool_call_id=ref,
                    tool_name="terminal",
                    status="ok",
                    turn_id="turn",
                )

    first_refs = [(event.kind, event.ref) for event in first.events]
    second_refs = [(event.kind, event.ref) for event in second.events]
    assert first_refs == second_refs
    sampled_calls = {
        event.ref for event in first.events if event.kind == "tool_call"
    }
    sampled_results = {
        event.ref for event in first.events if event.kind == "tool_result"
    }
    assert sampled_calls == sampled_results
    assert 0 < len(sampled_calls) < 50
    assert {"inbound", "turn"} <= {event.kind for event in first.events}


def test_action_tracking_config_bounds_invalid_values() -> None:
    settings = ActionTrackingConfig.from_config(
        {
            "action_tracking": {
                "enabled": False,
                "retention_days": 0,
                "rollup": False,
                "sample": 9,
            }
        }
    )
    assert settings == ActionTrackingConfig(
        enabled=False,
        retention_days=1,
        rollup=False,
        sample=1.0,
    )


def test_existing_tool_hooks_emit_c8_without_external_observer(
    monkeypatch,
) -> None:
    monkeypatch.setattr(plugins, "has_hook", lambda _name: False)
    monkeypatch.setattr(plugins, "invoke_hook", lambda _name, **_kwargs: [])
    trace = _trace()
    with bind_trace(trace):
        observe("inbound", ref="msg", summary="Inbound")
        observe("turn", ref="turn_1", summary="Turn")
        assert get_pre_tool_call_block_message(
            "terminal",
            {"command": "pwd"},
            tool_call_id="call_1",
            turn_id="turn_1",
        ) is None
        _emit_post_tool_call_hook(
            function_name="terminal",
            function_args={"command": "pwd"},
            result='{"output": "/tmp"}',
            tool_call_id="call_1",
            turn_id="turn_1",
            status="ok",
        )

    tool_events = [
        (event.kind, event.ref, event.parent_id)
        for event in trace.events
        if event.kind in {"tool_call", "tool_result"}
    ]
    assert tool_events[0][:2] == ("tool_call", "call_1")
    assert tool_events[1][:2] == ("tool_result", "call_1")
    assert tool_events[1][2] == trace.events[2].id


def test_tracing_does_not_change_prompt_bytes_or_message_alternation() -> None:
    stored = "Hermes cache prefix\nUnicode: ☤ — 🦊\n"
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]

    def restore(with_trace: bool) -> tuple[bytes, list[dict[str, str]]]:
        db = MagicMock()
        db.get_session.return_value = {"system_prompt": stored}
        agent = MagicMock()
        agent._cached_system_prompt = None
        agent.session_id = "sess_1"
        agent.model = "test-model"
        agent.provider = "openrouter"
        agent.platform = "cli"
        agent._session_db = db
        agent._build_system_prompt = MagicMock(return_value="rebuilt")
        before = [dict(message) for message in history]
        if with_trace:
            with bind_trace(_trace()):
                _restore_or_build_system_prompt(agent, None, history)
                observe("turn", ref="turn_1", summary="Turn")
        else:
            _restore_or_build_system_prompt(agent, None, history)
        assert history == before
        assert isinstance(agent._cached_system_prompt, str)
        return agent._cached_system_prompt.encode("utf-8"), before

    disabled_prompt, disabled_messages = restore(False)
    enabled_prompt, enabled_messages = restore(True)
    assert enabled_prompt == disabled_prompt == stored.encode("utf-8")
    assert enabled_messages == disabled_messages
    roles = [message["role"] for message in enabled_messages]
    assert all(left != right for left, right in zip(roles, roles[1:]))
