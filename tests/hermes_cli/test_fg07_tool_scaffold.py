"""Unit + real-path tests for the FG-07 in-house tool scaffolder.

These exercise the pure, DB-free surface of FG-07:

* :func:`hermes_cli.tools_registry.validate_tool_config` enforces the AGENTS.md
  "no new ``HERMES_*`` non-secret config" contract;
* :func:`hermes_cli.tool_scaffold.resolve_port` is deterministic + in-window;
* :func:`hermes_cli.tool_scaffold.scaffold_in_house_tool` writes a Next.js app
  (web UI, ``data-component`` root) + a dependency-free thin MCP server, and the
  generated tree contains no smuggled ``HERMES_*`` config;
* the generated ``mcp/server.mjs`` completes a **real** JSON-RPC handshake
  (``initialize`` → ``tools/list`` → ``tools/call ping``) under ``node`` — the
  agent-facing MCP interface actually works, not just its file shape.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_cli.tool_scaffold import (
    DEFAULT_PORT_BASE,
    DEFAULT_PORT_SPAN,
    resolve_port,
    scaffold_in_house_tool,
)
from hermes_cli.tools_registry import ToolConfigError, validate_tool_config


# ── config validation (no HERMES_* non-secret config) ──────────────────────

def test_validate_tool_config_accepts_plain_object() -> None:
    cfg = {"greeting": "hi", "nested": {"limit": 3, "flags": ["a", "b"]}}
    assert validate_tool_config(cfg) == cfg
    assert validate_tool_config(None) == {}


def test_validate_tool_config_rejects_non_mapping() -> None:
    with pytest.raises(ToolConfigError):
        validate_tool_config([1, 2, 3])


@pytest.mark.parametrize(
    "bad",
    [
        {"HERMES_FOO": "1"},
        {"hermes_bar": "1"},
        {"nested": {"HERMES_DEEP": "x"}},
        {"list": [{"HERMES_INNER": "y"}]},
    ],
)
def test_validate_tool_config_rejects_hermes_env_keys(bad: dict) -> None:
    with pytest.raises(ToolConfigError):
        validate_tool_config(bad)


# ── deterministic port assignment ───────────────────────────────────────────

def test_resolve_port_is_deterministic_and_in_window() -> None:
    a = resolve_port("invoices")
    b = resolve_port("invoices")
    assert a == b
    assert DEFAULT_PORT_BASE <= a < DEFAULT_PORT_BASE + DEFAULT_PORT_SPAN
    # Different names generally land on different ports.
    assert resolve_port("invoices") != resolve_port("calendar")


# ── scaffold output shape ────────────────────────────────────────────────────

def test_scaffold_writes_expected_files(tmp_path: Path) -> None:
    result = scaffold_in_house_tool("invoices", tmp_path, port=4321)

    assert result.name == "invoices"
    assert result.port == 4321
    assert result.web_url == "http://127.0.0.1:4321"
    assert result.mcp_transport() == {
        "type": "stdio",
        "command": "node",
        "args": ["mcp/server.mjs"],
    }

    project = tmp_path / "invoices"
    for rel in (
        "package.json",
        "next.config.mjs",
        "tsconfig.json",
        "app/layout.tsx",
        "app/page.tsx",
        "mcp/server.mjs",
        "tool.config.json",
        "README.md",
    ):
        assert (project / rel).is_file(), f"missing {rel}"
        assert rel in result.files

    # Web UI root carries the repo-standard data-component attribute.
    assert 'data-component="ToolHome"' in (project / "app/page.tsx").read_text()

    # package.json wires the tool's own Node process on its port + an MCP script.
    pkg = json.loads((project / "package.json").read_text())
    assert pkg["scripts"]["dev"] == "next dev -p 4321"
    assert pkg["scripts"]["mcp"] == "node mcp/server.mjs"
    assert "next" in pkg["dependencies"]

    # tool.config.json holds behavioural config — no HERMES_* env var anywhere.
    tool_cfg = json.loads((project / "tool.config.json").read_text())
    assert tool_cfg["stack"] == "nextjs-node"
    assert tool_cfg["port"] == 4321
    # No HERMES_* config key is smuggled into the generated code/config. The
    # README.md legitimately *documents* the no-HERMES_* rule in prose, so it's
    # excluded from the key scan.
    for path in project.rglob("*"):
        if path.is_file() and path.name != "README.md":
            assert "HERMES_" not in path.read_text(encoding="utf-8"), path


def test_scaffold_rejects_bad_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        scaffold_in_house_tool("../escape", tmp_path)


# ── real MCP handshake against the generated thin server ────────────────────

def _rpc(line: dict) -> str:
    return json.dumps(line) + "\n"


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_generated_mcp_server_handshakes(tmp_path: Path) -> None:
    result = scaffold_in_house_tool("weather", tmp_path, port=4380)
    server = result.root / "mcp" / "server.mjs"

    payload = (
        _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + _rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + _rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + _rpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {}},
            }
        )
    )

    proc = subprocess.run(
        ["node", str(server)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    by_id = {r.get("id"): r for r in responses}

    # initialize → serverInfo names the tool + advertises tools capability.
    assert by_id[1]["result"]["serverInfo"]["name"] == "weather"
    assert "tools" in by_id[1]["result"]["capabilities"]
    # tools/list → the thin server exposes exactly the ping tool.
    assert [t["name"] for t in by_id[2]["result"]["tools"]] == ["ping"]
    # tools/call ping → text content proving the process handled the call.
    assert by_id[3]["result"]["content"][0]["text"] == "weather ok"
