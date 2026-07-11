"""In-house tool scaffolder — ``hermes tool new <name>`` (FG-07 / decision D3).

An in-house tool is a **Next.js app in its own Node process** that exposes BOTH
a **web UI** (for humans) and a **thin MCP server** (for the agent). This module
only *generates the project on disk* and reports a structured
:class:`ScaffoldResult` (files written + the port + the web URL + the MCP stdio
transport). Registering the tool in the C2/C3 registry and materializing its MCP
endpoint into the FG-11 registry is the CLI command's job (``tool_cmd.py``), so
the pure file generation stays trivially unit-testable and free of any DB / live
-conversation coupling.

Design choices that keep this cache-safe and dependency-honest:

* The **thin MCP server** (``mcp/server.mjs``) is written in pure Node with
  **no npm dependencies** — it speaks line-delimited JSON-RPC over stdio
  (``initialize`` / ``tools/list`` / ``tools/call``). That means the agent's MCP
  interface is reachable with just ``node`` on the box (the E2E test drives a
  real handshake against it) without a Next.js/npm install, and the tool's MCP
  endpoint is a plain stdio transport the FG-11 registry already understands.
* The **web UI** is a minimal Next.js App-Router app; its own ``next dev`` /
  ``next start`` is the tool's independent Node process on its own port. Its
  root React element carries ``data-component`` per the repo web convention.
* The tool's **behavioural config** lives in ``tool.config.json`` (and the
  registry row's ``config_json``) — never a new ``HERMES_*`` env var.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

#: Deterministic port window for in-house tools (config-overridable, never an
#: env var). A tool's port is stable across scaffolds of the same name.
DEFAULT_PORT_BASE = 4300
DEFAULT_PORT_SPAN = 400

_SLUG = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class ScaffoldResult:
    """Structured description of a freshly scaffolded in-house tool."""

    name: str
    root: Path
    port: int
    web_url: str
    mcp_command: str
    mcp_args: List[str]
    files: List[str] = field(default_factory=list)

    def mcp_transport(self) -> dict:
        """The stdio MCP transport the FG-11 endpoint registry consumes."""
        return {
            "type": "stdio",
            "command": self.mcp_command,
            "args": list(self.mcp_args),
        }


def _slug(name: str) -> str:
    return _SLUG.sub("-", name.strip().lower()).strip("-") or "tool"


def resolve_port(name: str, *, base: int = DEFAULT_PORT_BASE,
                 span: int = DEFAULT_PORT_SPAN) -> int:
    """Deterministically map a tool name to a stable port in ``[base, base+span)``.

    Deterministic (not random) so re-scaffolding or restarting a tool reuses the
    same port, and so the registry's ``web_url`` stays valid across sessions.
    """
    digest = 0
    for char in name:
        digest = (digest * 31 + ord(char)) & 0xFFFFFFFF
    return base + (digest % span)


def scaffold_in_house_tool(
    name: str,
    root: Path,
    *,
    port: int | None = None,
    host: str = "127.0.0.1",
) -> ScaffoldResult:
    """Generate a Next.js + thin-MCP in-house tool under ``root/<name>``.

    Returns a :class:`ScaffoldResult`. Idempotent per file (overwrites), so a
    re-scaffold refreshes the template without touching sibling tools.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name or ""):
        raise ValueError(f"Invalid tool name: {name!r}")

    slug = _slug(name)
    tool_port = port if port is not None else resolve_port(name)
    web_url = f"http://{host}:{tool_port}"
    project = root / name
    (project / "app").mkdir(parents=True, exist_ok=True)
    (project / "mcp").mkdir(parents=True, exist_ok=True)

    written: List[str] = []

    def _write(relative: str, content: str) -> None:
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(relative)

    _write("package.json", _PACKAGE_JSON.format(slug=slug, port=tool_port))
    _write("next.config.mjs", _NEXT_CONFIG)
    _write("tsconfig.json", _TSCONFIG)
    _write("app/layout.tsx", _LAYOUT_TSX.format(name=name))
    _write("app/page.tsx", _PAGE_TSX.format(name=name, port=tool_port))
    _write("mcp/server.mjs", _MCP_SERVER_MJS.format(name=name))
    _write(
        "tool.config.json",
        json.dumps(
            {
                "name": name,
                "stack": "nextjs-node",
                "port": tool_port,
                "web_url": web_url,
                "mcp": {"command": "node", "args": ["mcp/server.mjs"]},
                # Behavioural config lives here (and in the registry row) — never
                # a HERMES_* env var. Secrets belong in the tool's own .env.
                "config": {},
            },
            indent=2,
        )
        + "\n",
    )
    _write("README.md", _README_MD.format(name=name, port=tool_port))

    return ScaffoldResult(
        name=name,
        root=project,
        port=tool_port,
        web_url=web_url,
        mcp_command="node",
        mcp_args=["mcp/server.mjs"],
        files=sorted(written),
    )


_PACKAGE_JSON = """\
{{
  "name": "{slug}",
  "version": "0.1.0",
  "private": true,
  "description": "Hermes in-house tool (Next.js web UI + thin MCP server).",
  "scripts": {{
    "dev": "next dev -p {port}",
    "build": "next build",
    "start": "next start -p {port}",
    "mcp": "node mcp/server.mjs"
  }},
  "dependencies": {{
    "next": "15.1.6",
    "react": "19.0.0",
    "react-dom": "19.0.0"
  }},
  "devDependencies": {{
    "typescript": "5.7.3",
    "@types/node": "22.10.7",
    "@types/react": "19.0.7"
  }}
}}
"""

_NEXT_CONFIG = """\
/** @type {import('next').NextConfig} */
const nextConfig = { reactStrictMode: true };
export default nextConfig;
"""

_TSCONFIG = """\
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "jsx": "preserve",
    "module": "esnext",
    "moduleResolution": "bundler",
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "plugins": [{ "name": "next" }]
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
  "exclude": ["node_modules", "mcp"]
}
"""

_LAYOUT_TSX = """\
export const metadata = {{ title: "{name}" }};

export default function RootLayout({{
  children,
}}: {{
  children: React.ReactNode;
}}) {{
  return (
    <html lang="en">
      <body>{{children}}</body>
    </html>
  );
}}
"""

# NOTE: the root element carries data-component per the repo web convention.
_PAGE_TSX = """\
export default function ToolHome() {{
  return (
    <main data-component="ToolHome" style={{{{ padding: "2rem", fontFamily: "sans-serif" }}}}>
      <h1>{name}</h1>
      <p>In-house Hermes tool. Web UI on port {port}.</p>
      <p>The agent talks to this tool through its thin MCP server (mcp/server.mjs).</p>
    </main>
  );
}}
"""

# Pure-Node, dependency-free thin MCP server: line-delimited JSON-RPC over
# stdio. Implements initialize / tools/list / tools/call so the agent (and the
# E2E test) can reach the tool without an npm install.
_MCP_SERVER_MJS = r"""#!/usr/bin/env node
// Thin MCP server for the "{name}" in-house tool.
// Line-delimited JSON-RPC 2.0 over stdio — no npm dependencies.
"use strict";

const TOOL_NAME = "{name}";

const TOOLS = [
  {{
    name: "ping",
    description: "Health check for the " + TOOL_NAME + " in-house tool.",
    inputSchema: {{ type: "object", properties: {{}} }},
  }},
];

function respond(id, result) {{
  process.stdout.write(JSON.stringify({{ jsonrpc: "2.0", id, result }}) + "\n");
}}

function fail(id, code, message) {{
  process.stdout.write(
    JSON.stringify({{ jsonrpc: "2.0", id, error: {{ code, message }} }}) + "\n"
  );
}}

function handle(msg) {{
  const {{ id, method, params }} = msg;
  if (method === "initialize") {{
    respond(id, {{
      protocolVersion: "2024-11-05",
      serverInfo: {{ name: TOOL_NAME, version: "0.1.0" }},
      capabilities: {{ tools: {{}} }},
    }});
  }} else if (method === "tools/list") {{
    respond(id, {{ tools: TOOLS }});
  }} else if (method === "tools/call") {{
    const name = params && params.name;
    if (name === "ping") {{
      respond(id, {{
        content: [{{ type: "text", text: TOOL_NAME + " ok" }}],
      }});
    }} else {{
      fail(id, -32601, "Unknown tool: " + name);
    }}
  }} else if (method === "notifications/initialized") {{
    // notification — no response
  }} else if (id !== undefined) {{
    fail(id, -32601, "Unknown method: " + method);
  }}
}}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {{
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {{
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (!line) continue;
    try {{
      handle(JSON.parse(line));
    }} catch (err) {{
      fail(null, -32700, "Parse error: " + err.message);
    }}
  }}
}});
"""

_README_MD = """\
# {name}

An in-house Hermes tool: a Next.js app running in its own Node process that
exposes a **web UI** (humans) and a **thin MCP server** (the agent).

## Web UI

```bash
npm install
npm run dev        # serves the UI on http://127.0.0.1:{port}
```

## MCP server (agent interface)

```bash
npm run mcp        # node mcp/server.mjs — JSON-RPC over stdio, no deps
```

The Hermes tool registry records this tool's MCP endpoint (via FG-11) so future
agent sessions can reach it. Configuration lives in `tool.config.json` and the
registry row's `config_json` — never in a `HERMES_*` env var (secrets only, in
this tool's own `.env`).
"""


__all__ = [
    "ScaffoldResult",
    "scaffold_in_house_tool",
    "resolve_port",
    "DEFAULT_PORT_BASE",
    "DEFAULT_PORT_SPAN",
]
