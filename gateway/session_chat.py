"""Shared one-brain session-chat helper.

A single place that builds an :class:`AIAgent` for a *persisted session* using
the gateway's runtime config (model, provider fallback, reasoning config,
per-platform toolsets) and the shared ``SessionDB``. Both the OpenAI-compatible
API-server platform adapter and the dashboard's ``/api/sessions/{id}/chat``
endpoint drive one-brain chat through this helper so there is exactly **one**
agent-construction path — the brain, its toolset resolution, and its session
persistence are identical across surfaces.

This module deliberately contains no HTTP/auth logic and no per-request state:
callers own auth (C1 principal), the ``SessionDB`` handle, and the runtime
session-context binding (:mod:`gateway.session_context`). It only assembles the
agent and (optionally) runs one turn in a worker thread.
"""

from typing import Any, List, Optional, Tuple


def build_session_agent(
    *,
    session_db: Any,
    ephemeral_system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
    stream_delta_callback=None,
    tool_progress_callback=None,
    tool_start_callback=None,
    tool_complete_callback=None,
    gateway_session_key: Optional[str] = None,
    platform: str = "api_server",
) -> Any:
    """Create an :class:`AIAgent` for a persisted session.

    Uses ``_resolve_runtime_agent_kwargs()`` for model/api_key/base_url,
    resolves toolsets from ``config.yaml``'s ``platform_toolsets.<platform>``
    (defaulting to the shared ``api_server`` one-brain surface), and wires the
    provided ``session_db`` so the turn is persisted to the same store every
    other surface reads. Does not mutate prior context or the system prompt —
    prompt caching / strict alternation are preserved by the agent loop.
    """
    from run_agent import AIAgent
    from gateway.run import (
        _current_max_iterations,
        _resolve_runtime_agent_kwargs,
        _resolve_gateway_model,
        _load_gateway_config,
        GatewayRunner,
    )
    from hermes_cli.tools_config import _get_platform_tools

    runtime_kwargs = _resolve_runtime_agent_kwargs()
    reasoning_config = GatewayRunner._load_reasoning_config()
    model = _resolve_gateway_model()

    # When the primary provider's auth fails, _resolve_runtime_agent_kwargs()
    # falls through to a fallback provider whose runtime dict carries its own
    # ``model`` key; pop it so it doesn't collide with the explicit ``model=``
    # below (mirrors the api_server / native gateway path).
    runtime_model = runtime_kwargs.pop("model", None)
    if runtime_model:
        model = runtime_model

    user_config = _load_gateway_config()
    enabled_toolsets = sorted(_get_platform_tools(user_config, platform))
    max_iterations = _current_max_iterations()
    fallback_model = GatewayRunner._load_fallback_model()

    return AIAgent(
        model=model,
        **runtime_kwargs,
        max_iterations=max_iterations,
        quiet_mode=True,
        verbose_logging=False,
        ephemeral_system_prompt=ephemeral_system_prompt or None,
        enabled_toolsets=enabled_toolsets,
        session_id=session_id,
        platform=platform,
        stream_delta_callback=stream_delta_callback,
        tool_progress_callback=tool_progress_callback,
        tool_start_callback=tool_start_callback,
        tool_complete_callback=tool_complete_callback,
        session_db=session_db,
        fallback_model=fallback_model,
        reasoning_config=reasoning_config,
        gateway_session_key=gateway_session_key,
    )


def run_session_turn_sync(
    *,
    session_db: Any,
    user_message: str,
    conversation_history: List[dict],
    session_id: Optional[str] = None,
    ephemeral_system_prompt: Optional[str] = None,
    gateway_session_key: Optional[str] = None,
    stream_delta_callback=None,
    tool_progress_callback=None,
    platform: str = "api_server",
) -> Tuple[dict, dict]:
    """Run one synchronous one-brain turn for a persisted session.

    Blocking; intended to be dispatched to a worker thread by async callers
    (``await loop.run_in_executor(None, ...)``). Returns ``(result, usage)``
    where *result* is the ``AIAgent.run_conversation`` dict (``final_response``,
    ``session_id``) and *usage* holds token counts. Does not bind runtime
    session-context — the caller sets/clears that around this call.
    """
    agent = build_session_agent(
        session_db=session_db,
        ephemeral_system_prompt=ephemeral_system_prompt,
        session_id=session_id,
        stream_delta_callback=stream_delta_callback,
        tool_progress_callback=tool_progress_callback,
        gateway_session_key=gateway_session_key,
        platform=platform,
    )
    result = agent.run_conversation(
        user_message=user_message,
        conversation_history=conversation_history,
        task_id=session_id or "",
    )
    usage = {
        "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
        "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
        "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
    }
    eff_sid = getattr(agent, "session_id", session_id)
    if isinstance(eff_sid, str) and eff_sid and isinstance(result, dict):
        result["session_id"] = eff_sid
    return result, usage
