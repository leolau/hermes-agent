"""Unit tests for the FG-11 principal-aware MCP surface (no database).

Verifies the behaviours that must hold without a live app store: an
unauthenticated peer resolves to an anonymous ``viewer`` (least privilege),
and a ``viewer`` is refused the mutating ``memory_add`` tool before any store
access. Real-path C2 scoping + C6-gated writes are covered against a throwaway
Postgres in ``tests/hermes_cli/test_fg11_agent_comms_e2e.py``.
"""

from __future__ import annotations

import inspect
import json

import pytest


class _FakeTool:
    def __init__(self, fn):
        self.name = fn.__name__
        self.description = inspect.getdoc(fn) or ""
        self.fn = fn


class _FakeToolManager:
    def __init__(self):
        self._tools = {}

    def add_tool(self, fn):
        self._tools[fn.__name__] = _FakeTool(fn)

    def list_tools(self):
        return list(self._tools.values())


class _FakeFastMCP:
    def __init__(self, *args, **kwargs):
        self._tool_manager = _FakeToolManager()

    def tool(self):
        def decorator(fn):
            self._tool_manager.add_tool(fn)
            return fn

        return decorator


@pytest.fixture
def scoped_server(monkeypatch):
    import mcp_serve

    monkeypatch.setattr(mcp_serve, "_MCP_SERVER_AVAILABLE", True)
    monkeypatch.setattr(mcp_serve, "FastMCP", _FakeFastMCP)
    monkeypatch.delenv(mcp_serve.MCP_PRINCIPAL_ENV, raising=False)
    server = mcp_serve.create_mcp_server()
    return server


def _tool(server, name):
    return server._tool_manager._tools[name].fn


def test_scoped_tools_are_registered(scoped_server):
    names = {t.name for t in scoped_server._tool_manager.list_tools()}
    assert {"whoami", "memory_search", "memory_add"} <= names


@pytest.mark.asyncio
async def test_whoami_unauthenticated_is_anonymous_viewer(scoped_server):
    result = json.loads(await _tool(scoped_server, "whoami")())
    assert result["role"] == "viewer"
    assert result["authenticated"] is False
    assert result["user_id"] == "mcp:anonymous"


@pytest.mark.asyncio
async def test_viewer_cannot_add_memory(scoped_server, monkeypatch):
    import mcp_serve

    # A store accessor that must never be reached — the role gate rejects a
    # viewer before any write path executes.
    def _boom(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("viewer write must not reach the memory store")

    monkeypatch.setattr(mcp_serve, "_get_memory_store", _boom)
    result = json.loads(
        await _tool(scoped_server, "memory_add")(text="secret")
    )
    assert "viewer" in result["error"]
