"""Unit tests for the FG-11 MCP endpoint registry (no database).

Covers the pure logic that must hold regardless of a live Postgres: transport
validation, the ``mcp_servers`` config translation the client consumes, C2
visibility resolution, and the role gate that stops a ``viewer`` registering.
The real-path C2/mode E2E lives in ``test_fg11_agent_comms_e2e.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from hermes_cli.access import Principal
from hermes_cli.mcp_endpoints import (
    MCPEndpoint,
    MCPEndpointRegistry,
    _resolve_visibility,
    _validate_transport,
)


def _member(user_id: str = "alice") -> Principal:
    return Principal(user_id=user_id, display=user_id, role="member")


def _owner() -> Principal:
    return Principal(user_id="root", display="root", role="owner")


def _viewer() -> Principal:
    return Principal(user_id="mcp:anonymous", display="anon", role="viewer")


class TestTransportValidation:
    def test_stdio_requires_command(self):
        with pytest.raises(ValueError, match="command"):
            _validate_transport({"type": "stdio"})

    def test_http_requires_url(self):
        with pytest.raises(ValueError, match="url"):
            _validate_transport({"type": "http"})

    def test_rejects_unknown_transport(self):
        with pytest.raises(ValueError, match="stdio.*http|type"):
            _validate_transport({"type": "grpc"})

    def test_stdio_normalizes_args_and_env(self):
        t = _validate_transport(
            {"type": "stdio", "command": "npx", "args": ["-y", "pkg"], "env": {"K": "V"}}
        )
        assert t == {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "pkg"],
            "env": {"K": "V"},
        }

    def test_stdio_bad_args_type(self):
        with pytest.raises(ValueError, match="args"):
            _validate_transport({"type": "stdio", "command": "x", "args": "notalist"})


class TestConfigTranslation:
    def test_http_to_server_config(self):
        ep = MCPEndpoint(
            id="mep_1",
            name="remote1",
            kind="remote",
            transport={"type": "http", "url": "https://h/mcp", "auth": "oauth"},
            owner_user_id="root",
            visibility="shared",
            mode="prod",
        )
        assert ep.to_server_config() == {"url": "https://h/mcp", "auth": "oauth"}

    def test_stdio_to_server_config(self):
        ep = MCPEndpoint(
            id="mep_2",
            name="inhouse1",
            kind="in_house",
            transport={"type": "stdio", "command": "npx", "args": ["a"], "env": {"K": "V"}},
            owner_user_id="root",
            visibility="private:root",
            mode="dev",
        )
        assert ep.to_server_config() == {
            "command": "npx",
            "args": ["a"],
            "env": {"K": "V"},
        }


class TestVisibilityResolution:
    def test_member_default_is_own_private(self):
        assert _resolve_visibility(_member(), None) == "private:alice"

    def test_member_shared_passthrough(self):
        assert _resolve_visibility(_member(), "shared") == "shared"

    def test_member_cannot_register_other_private(self):
        with pytest.raises(PermissionError):
            _resolve_visibility(_member(), "private:bob")

    def test_owner_can_register_other_private(self):
        assert _resolve_visibility(_owner(), "private:bob") == "private:bob"


class TestRoleGate:
    def test_viewer_cannot_register(self):
        # The role gate fails before any connection is attempted, so a store
        # that would explode on connect proves no DB access happened.
        class _ExplodingStore:
            mode = "prod"
            schema = "app_prod"

            async def connect(self):  # pragma: no cover - must never run
                raise AssertionError("viewer registration must not hit the store")

        registry = MCPEndpointRegistry(_ExplodingStore())
        with pytest.raises(PermissionError, match="viewer"):
            asyncio.run(
                registry.register(
                    _viewer(),
                    "x",
                    "remote",
                    {"type": "http", "url": "https://h/mcp"},
                )
            )
