"""``hermes tool`` — create/manage in-house tools + the C2/C3 tool registry.

This is the CLI rung of the footprint ladder for FG-07: authoring an in-house
tool (a Next.js app in its own Node process with a web UI + thin MCP server) and
managing the mode-aware, scope-aware tool registry are expressed as
``hermes tool <sub>`` commands — **zero** model-tool footprint, no new core
surface, and nothing that mutates a live conversation's prompt/toolset.

Subcommands:

* ``new <name>`` — scaffold an in-house tool in **dev** mode, register it in the
  C2/C3 :class:`~hermes_cli.tools_registry.ToolRegistry`, and materialize its
  MCP endpoint into the FG-11 :class:`~hermes_cli.mcp_endpoints.MCPEndpointRegistry`
  (for a *future* session — never spliced live).
* ``list`` — list tools visible to the operator (C2-scoped) in a mode.
* ``enable`` / ``disable`` — flip a tool's status (owner or owner-role).
* ``config <name> --json ... | --file ...`` — replace a tool's ``config_json``
  (validated: no ``HERMES_*`` non-secret keys).
* ``promote <name>`` — approval-gated (C6) dev→prod promotion, recording the
  shared C5 approval/change/promotion rows.

Routine dev authoring/enable/disable/config each record a C5 provenance change
(pre-approved — the operator is performing it directly); only ``promote`` is
C6-gated.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from hermes_cli.access import Principal


def _load_config_arg(args) -> dict:
    raw = getattr(args, "json", None)
    file = getattr(args, "file", None)
    if raw and file:
        raise SystemExit("Specify either --json or --file, not both")
    if file:
        raw = Path(file).read_text(encoding="utf-8")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON config: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("Tool config must be a JSON object")
    return parsed


async def _resolve_operator(store, as_user: Optional[str]) -> Principal:
    """Resolve the operator principal: ``--as <user>`` or the enrolled owner."""
    from hermes_cli.access import PrincipalStore

    principals = PrincipalStore(store)
    if as_user:
        principal = await principals.get(as_user)
        if principal is None:
            raise SystemExit(
                f"No principal enrolled for {as_user!r}. Enrol the user first."
            )
        return principal
    owner = await principals.get_owner()
    if owner is None:
        raise SystemExit(
            "No owner enrolled yet — enrol the owner first, or pass --as <user>."
        )
    return owner


async def _record_provenance(
    prod_store,
    *,
    actor_user_id: str,
    action: str,
    target_ref: str,
    mode: str,
    visibility: str,
    payload: dict,
) -> None:
    """Record a pre-approved C5 provenance change for a routine tool op.

    Best-effort: the tool registry is the source of truth; if the shared change
    log is unavailable the mutation already succeeded, so we surface a warning
    rather than fail the command.
    """
    from hermes_cli.changes import ChangeLog, initialize_changes

    op = [{"op": "record", "path": f"/tools/{target_ref}", "value": payload}]
    try:
        connection = await prod_store.connect()
        try:
            await initialize_changes(connection)
        finally:
            await connection.close()
        await ChangeLog(prod_store).record(
            actor_user_id=actor_user_id,
            target_kind="code",
            op=op,
            inverse_op=op,
            reversible=True,
            action=action,
            target_ref=target_ref,
            mode=mode,
            visibility=visibility,
            payload=payload,
            approved=True,
        )
    except Exception as exc:  # noqa: BLE001 - provenance is best-effort
        print(f"warning: could not record provenance change: {exc}")


async def _run(args, action: str) -> None:
    from hermes_cli.datastore import get_store, resolve_mode
    from hermes_cli.tools_registry import ToolRegistry, promote_tool

    mode = getattr(args, "mode", None) or resolve_mode(None)
    prod_store = get_store("supabase-app", "prod")
    principal = await _resolve_operator(prod_store, getattr(args, "as_user", None))

    registry = ToolRegistry(get_store("supabase-app", mode))
    await registry.initialize()

    if action == "new":
        await _cmd_new(args, principal, registry, prod_store, mode)
        return

    if action == "list":
        tools = await registry.list_for_principal(principal)
        if not tools:
            print(f"No tools visible to {principal.user_id!r} in mode {mode}.")
            return
        print(f"Tools visible to {principal.user_id!r} (mode={mode}):")
        for tool in tools:
            print(
                f"  - {tool.name}  [{tool.kind}]  status={tool.status}  "
                f"visibility={tool.visibility}  "
                f"web_url={tool.web_url or '-'}  "
                f"mcp={tool.mcp_endpoint_ref or '-'}"
            )
        return

    if action in ("enable", "disable"):
        tool = await registry.set_enabled(principal, args.name, action == "enable")
        await _record_provenance(
            prod_store,
            actor_user_id=principal.user_id,
            action=f"hermes tool {action} {tool.name}",
            target_ref=tool.name,
            mode=tool.mode,
            visibility=tool.visibility,
            payload={"status": tool.status},
        )
        print(f"Tool {tool.name!r} is now {tool.status} (mode={tool.mode}).")
        return

    if action == "config":
        config = _load_config_arg(args)
        tool = await registry.set_config(principal, args.name, config)
        await _record_provenance(
            prod_store,
            actor_user_id=principal.user_id,
            action=f"hermes tool config {tool.name}",
            target_ref=tool.name,
            mode=tool.mode,
            visibility=tool.visibility,
            payload={"config_json": tool.config_json},
        )
        print(f"Updated config for tool {tool.name!r} (mode={tool.mode}).")
        return

    if action == "promote":
        result = await promote_tool(
            get_store("supabase-app", "prod"),
            args.name,
            actor=principal.user_id,
        )
        print(
            f"Promoted tool {result.tool_name!r} dev→prod.\n"
            f"  approval={result.approval_ref}\n"
            f"  change={result.change_ref}\n"
            f"  promotion={result.promotion_ref}"
        )
        return


async def _cmd_new(args, principal, registry, prod_store, mode) -> None:
    from hermes_cli.mcp_endpoints import MCPEndpointRegistry
    from hermes_cli.tool_scaffold import scaffold_in_house_tool
    from hermes_constants import get_hermes_home

    name = args.name
    tools_root = Path(getattr(args, "root", "") or (get_hermes_home() / "tools"))
    scaffold = scaffold_in_house_tool(name, tools_root)

    # Materialize the tool's MCP endpoint into the FG-11 registry (future
    # sessions only — never spliced into a live conversation).
    visibility = "shared" if getattr(args, "shared", False) else None
    endpoints = MCPEndpointRegistry(registry.store)  # same mode store
    await endpoints.initialize()
    endpoint = await endpoints.register(
        principal,
        name,
        "in_house",
        scaffold.mcp_transport(),
        visibility=visibility,
    )

    tool = await registry.create(
        principal,
        name,
        "in_house",
        stack="nextjs-node",
        visibility=visibility,
        status="disabled",
        mcp_endpoint_ref=endpoint.name,
        web_url=scaffold.web_url,
        config={},
    )
    await _record_provenance(
        prod_store,
        actor_user_id=principal.user_id,
        action=f"hermes tool new {name}",
        target_ref=name,
        mode=tool.mode,
        visibility=tool.visibility,
        payload={"kind": "in_house", "stack": tool.stack, "web_url": tool.web_url},
    )

    print(f"Scaffolded in-house tool {name!r} in mode {mode}:")
    print(f"  path:    {scaffold.root}")
    print(f"  web UI:  {scaffold.web_url}  (npm install && npm run dev)")
    print(f"  MCP:     node {' '.join(scaffold.mcp_args)}  (npm run mcp)")
    print(f"  files:   {', '.join(scaffold.files)}")
    print(
        f"Registered in the tool registry (status={tool.status}) and the FG-11 "
        f"MCP endpoint registry (endpoint={endpoint.name})."
    )
    print("Enable it with `hermes tool enable %s`, then promote with "
          "`hermes tool promote %s`." % (name, name))


def cmd_tool(args) -> None:
    """Dispatch ``hermes tool`` subcommands (default ``list``)."""
    action = getattr(args, "tool_action", None) or "list"
    asyncio.run(_run(args, action))


def register_tool_subparser(subparsers) -> None:
    """Wire the ``hermes tool`` parser (FG-07 in-house tools + registry)."""
    parser = subparsers.add_parser(
        "tool",
        help="Create and manage in-house tools + the tool registry (FG-07)",
        description=(
            "Create in-house tools (Next.js app + thin MCP server, own Node "
            "process) and manage the mode-aware, scope-aware tool registry."
        ),
    )
    parser.add_argument(
        "--as", dest="as_user", default=None,
        help="Operate as this user_id (defaults to the enrolled owner)",
    )
    parser.add_argument(
        "--mode", choices=("dev", "prod"), default=None,
        help="Datastore mode (defaults to config datastore.mode / prod)",
    )
    sub = parser.add_subparsers(dest="tool_action")

    new = sub.add_parser("new", help="Scaffold + register a new in-house tool (dev)")
    new.add_argument("name", help="Tool name (also the project directory)")
    new.add_argument(
        "--root", default=None,
        help="Parent directory for the scaffold (default: $HERMES_HOME/tools)",
    )
    new.add_argument(
        "--shared", action="store_true",
        help="Register as shared instead of private to the operator",
    )
    new.set_defaults(func=cmd_tool, tool_action="new")

    listp = sub.add_parser("list", help="List tools visible to the operator")
    listp.set_defaults(func=cmd_tool, tool_action="list")

    enable = sub.add_parser("enable", help="Enable a tool")
    enable.add_argument("name")
    enable.set_defaults(func=cmd_tool, tool_action="enable")

    disable = sub.add_parser("disable", help="Disable a tool")
    disable.add_argument("name")
    disable.set_defaults(func=cmd_tool, tool_action="disable")

    config = sub.add_parser("config", help="Replace a tool's config_json")
    config.add_argument("name")
    config.add_argument("--json", default=None, help="Config as a JSON object string")
    config.add_argument("--file", default=None, help="Path to a JSON config file")
    config.set_defaults(func=cmd_tool, tool_action="config")

    promote = sub.add_parser(
        "promote", help="Approval-gated dev→prod promotion of a tool (C6)"
    )
    promote.add_argument("name")
    promote.set_defaults(func=cmd_tool, tool_action="promote")

    parser.set_defaults(func=cmd_tool)


__all__ = ["cmd_tool", "register_tool_subparser"]
