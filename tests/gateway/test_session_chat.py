"""Unit tests for the shared one-brain session-chat helper.

Covers the single agent-construction path used by both the API-server platform
adapter and the dashboard ``/api/sessions/{id}/chat`` endpoint:

* ``build_session_agent`` composes the gateway runtime config (model, fallback
  chain, reasoning, per-platform toolset, SessionDB) and — critically for
  prompt-cache safety — passes **no** ephemeral system prompt by default;
* ``run_session_turn_sync`` runs one turn, forwarding the conversation history
  *verbatim* (no synthetic messages) and surfacing the effective session id +
  token usage;
* the API-server adapter's ``_create_agent`` delegates to the shared builder,
  so the two surfaces cannot drift apart.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def stub_runtime(monkeypatch):
    """Stub the gateway runtime resolvers + AIAgent, capturing agent kwargs."""
    captured: dict = {}

    import gateway.run as grun
    import hermes_cli.tools_config as tools_config
    import run_agent

    monkeypatch.setattr(grun, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "k", "base_url": "u"})
    monkeypatch.setattr(grun, "_resolve_gateway_model", lambda: "test-model")
    monkeypatch.setattr(grun, "_load_gateway_config", lambda: {"stub": True})
    monkeypatch.setattr(grun, "_current_max_iterations", lambda: 7)
    monkeypatch.setattr(grun.GatewayRunner, "_load_reasoning_config", staticmethod(lambda: {"effort": "low"}))
    monkeypatch.setattr(grun.GatewayRunner, "_load_fallback_model", staticmethod(lambda: {"model": "fb"}))
    monkeypatch.setattr(tools_config, "_get_platform_tools", lambda cfg, platform: {"beta", "alpha"})

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            captured["_kwargs"] = kwargs
            self.session_id = kwargs.get("session_id")
            self.session_prompt_tokens = 11
            self.session_completion_tokens = 22
            self.session_total_tokens = 33

        def run_conversation(self, *, user_message, conversation_history, task_id):
            captured["run_user_message"] = user_message
            captured["run_history"] = conversation_history
            captured["run_task_id"] = task_id
            return {"final_response": f"reply:{user_message}"}

    monkeypatch.setattr(run_agent, "AIAgent", _FakeAgent)
    return captured


def test_build_session_agent_composes_runtime(stub_runtime):
    from gateway.session_chat import build_session_agent

    sentinel_db = object()
    agent = build_session_agent(session_db=sentinel_db, session_id="s1")

    kw = stub_runtime["_kwargs"]
    assert kw["model"] == "test-model"
    assert kw["api_key"] == "k" and kw["base_url"] == "u"
    assert kw["max_iterations"] == 7
    assert kw["reasoning_config"] == {"effort": "low"}
    assert kw["fallback_model"] == {"model": "fb"}
    assert kw["session_db"] is sentinel_db
    assert kw["platform"] == "api_server"
    assert kw["session_id"] == "s1"
    # Toolsets are resolved from config and sorted deterministically.
    assert kw["enabled_toolsets"] == ["alpha", "beta"]
    # Prompt-cache safety: no ephemeral system prompt injected by default.
    assert kw["ephemeral_system_prompt"] is None
    assert agent.session_id == "s1"


def test_build_session_agent_respects_platform_override(stub_runtime, monkeypatch):
    import hermes_cli.tools_config as tools_config
    from gateway.session_chat import build_session_agent

    seen = {}

    def _fake_tools(cfg, platform):
        seen["platform"] = platform
        return {"t"}

    monkeypatch.setattr(tools_config, "_get_platform_tools", _fake_tools)
    build_session_agent(session_db=object(), platform="agent_home")
    assert seen["platform"] == "agent_home"
    assert stub_runtime["_kwargs"]["platform"] == "agent_home"


def test_run_session_turn_forwards_history_verbatim(stub_runtime):
    from gateway.session_chat import run_session_turn_sync

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result, usage = run_session_turn_sync(
        session_db=object(),
        user_message="how are you?",
        conversation_history=history,
        session_id="sess-9",
    )

    # History passed through unchanged — no synthetic user message appended.
    assert stub_runtime["run_history"] == history
    assert stub_runtime["run_user_message"] == "how are you?"
    assert stub_runtime["run_task_id"] == "sess-9"
    assert result["final_response"] == "reply:how are you?"
    # The effective session id is stamped back onto the result.
    assert result["session_id"] == "sess-9"
    assert usage == {"input_tokens": 11, "output_tokens": 22, "total_tokens": 33}


def test_api_server_create_agent_delegates_to_shared_builder(monkeypatch):
    """The api_server adapter must build via the shared helper (no drift)."""
    import gateway.session_chat as session_chat
    from gateway.platforms.api_server import APIServerAdapter as ApiServerAdapter

    calls = {}

    def _fake_build(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(**{"built": True})

    monkeypatch.setattr(session_chat, "build_session_agent", _fake_build)

    adapter = ApiServerAdapter.__new__(ApiServerAdapter)
    adapter._session_db = object()
    monkeypatch.setattr(adapter, "_ensure_session_db", lambda: adapter._session_db, raising=False)

    agent = adapter._create_agent(session_id="abc", gateway_session_key="k")
    assert getattr(agent, "built", False) is True
    assert calls["platform"] == "api_server"
    assert calls["session_id"] == "abc"
    assert calls["gateway_session_key"] == "k"
    assert calls["session_db"] is adapter._session_db
