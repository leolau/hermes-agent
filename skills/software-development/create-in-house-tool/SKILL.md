---
name: create-in-house-tool
description: Scaffold and manage an in-house tool (Next.js web UI + thin MCP server, its own Node process) via the mode-aware, scope-aware tool registry.
version: 1.0.0
author: Leo Lau (@leolau), Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [tools, registry, in-house, mcp, nextjs, dev-prod, approval]
    category: software-development
    related_skills: [promote-artifact]
---

# Create In-House Tool Skill

Author an **in-house tool** — a Next.js app running in its own Node process that
exposes BOTH a human **web UI** and a thin **MCP server** for the agent — and
register it in the C3-routed, C2-scoped tool registry. New tools start in `dev`
and reach `prod` only through an approval-gated promotion.

## When to Use

- The user wants a new in-house system/tool with a dashboard AND an
  agent-callable interface.
- You need to enable/disable, reconfigure, or promote an existing registry tool.

Do not use this skill to add a new *core* model tool, to splice a tool into a
live conversation, or to copy application/user data between modes.

## Prerequisites

- The Supabase/Postgres DSN is configured at `datastore.supabase_app.dsn` in
  `config.yaml` (preferably a `${DATABASE_URL}` reference).
- `node` is on `PATH` (the scaffold and its MCP server run under Node).
- The operator is the enrolled owner, or pass `--as <user_id>`.

## How to Run

All commands share `--as <user_id>` and `--mode {dev,prod}` (default: the
config `datastore.mode`, else `prod`). Author in `dev`:

```bash
hermes tool new <name>                 # scaffold + register (disabled) in dev
hermes tool list --mode dev            # tools visible to the operator
hermes tool enable <name> --mode dev   # turn the tool on
hermes tool disable <name> --mode dev
hermes tool config <name> --file ./cfg.json   # or: --json '{"k": "v"}'
hermes tool promote <name>             # approval-gated dev→prod (C6)
```

`hermes tool new` scaffolds under `$HERMES_HOME/tools/<name>` (override with
`--root`), assigns a deterministic port, generates the Next.js app +
`mcp/server.mjs`, registers the MCP endpoint (FG-11) for **future** sessions,
and inserts a **disabled** in-house row in the `dev` registry. Add `--shared`
to register the tool as shared instead of private to the operator.

## Procedure

1. Run `hermes tool new <name>` and read the printed scaffold path, web URL,
   MCP endpoint, and port.
2. `cd` into the scaffold, `npm install`, then `npm run dev` (web UI) — the MCP
   server is `npm run mcp`. Iterate on the tool in `dev`.
3. Put behavioural settings in the generated `tool.config.json` /
   `hermes tool config`; never introduce a `HERMES_*` env var for non-secret
   config (secrets go in the tool's own `.env`).
4. `hermes tool enable <name> --mode dev` and validate the web UI + a real MCP
   `initialize` → `tools/list` → `tools/call` handshake.
5. When ready, use the **promote-artifact** procedure (or
   `hermes tool promote <name>`) to get explicit approval and land the
   definition in `prod`. It arrives **disabled**; enable it explicitly in prod.

## Pitfalls

- **Prompt-cache safety is sacred.** A newly created/registered tool must never
  mutate a running conversation's system prompt or already-resolved toolset; it
  only surfaces in a *fresh* session.
- Promotion moves the tool **definition** only — never application/user data.
  The promoted tool lands `disabled` in prod.
- C2 scoping: a viewer cannot author or mutate tools; a non-owner cannot mutate
  another owner's private tool and never sees another member's private tool.
- Each tool runs in its own Node process on its own port — do not share a
  process or port between tools.
- No new `HERMES_*` env vars for non-secret config.

## Verification

- `hermes tool list` shows the new tool in `dev` (and, after promotion, in
  `prod`).
- The web URL responds and the `mcp/server.mjs` completes a JSON-RPC handshake.
- After promotion, the C6 approval, C5 change-event, and promotion row are all
  linked, and dev-only tools remain invisible through prod reads.
