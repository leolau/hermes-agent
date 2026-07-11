"""``hermes mcp endpoints`` — uniform registration for the FG-11 registry.

This is the CLI rung of the footprint ladder for agent-comms: it reuses the
same stdio/http transport shape as ``hermes mcp add`` but records rows in the
mode-aware, C2-scoped :class:`hermes_cli.mcp_endpoints.MCPEndpointRegistry`
(contract-C3 routed) instead of the local ``config.yaml`` ``mcp_servers`` map.
Registered endpoints are materialized into ``mcp_servers`` config for a
*future* session's MCP client — never spliced into a live conversation.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional


def _transport_from_args(args) -> dict:
    """Build a transport spec from ``--command``/``--args``/``--env`` or ``--url``."""
    url = getattr(args, "url", None)
    command = getattr(args, "mcp_command", None)
    if url and command:
        raise ValueError("Specify either --url or --command, not both")
    if url:
        transport: dict = {"type": "http", "url": url}
        auth = getattr(args, "auth", None)
        if auth:
            transport["auth"] = auth
        return transport
    if command:
        transport = {"type": "stdio", "command": command}
        cmd_args = getattr(args, "args", None) or []
        if cmd_args:
            transport["args"] = list(cmd_args)
        env_pairs = getattr(args, "env", None) or []
        env: dict = {}
        for pair in env_pairs:
            if "=" not in pair:
                raise ValueError(f"--env expects KEY=VALUE, got {pair!r}")
            key, value = pair.split("=", 1)
            env[key] = value
        if env:
            transport["env"] = env
        return transport
    raise ValueError("An endpoint needs a transport: pass --url or --command")


async def _resolve_operator(store, as_user: Optional[str]):
    """Resolve the operator principal: ``--as <user>`` or the enrolled owner."""
    from hermes_cli.access import PrincipalStore

    principals = PrincipalStore(store)
    if as_user:
        principal = await principals.get(as_user)
        if principal is None:
            raise SystemExit(
                f"No principal enrolled for {as_user!r}. "
                f"Run `hermes owner` / enrol the user first."
            )
        return principal
    owner = await principals.get_owner()
    if owner is None:
        raise SystemExit(
            "No owner enrolled yet — enrol the owner before registering "
            "MCP endpoints, or pass --as <user_id>."
        )
    return owner


async def _run(args, action: str) -> None:
    from hermes_cli.datastore import get_store, resolve_mode
    from hermes_cli.mcp_endpoints import MCPEndpointRegistry

    mode = getattr(args, "mode", None) or resolve_mode(None)
    # Principals live in prod; endpoints live in the requested mode's schema.
    prod_store = get_store("supabase-app", "prod")
    principal = await _resolve_operator(prod_store, getattr(args, "as_user", None))

    registry = MCPEndpointRegistry(get_store("supabase-app", mode))
    await registry.initialize()

    if action == "register":
        transport = _transport_from_args(args)
        visibility = "shared" if getattr(args, "shared", False) else None
        endpoint = await registry.register(
            principal,
            args.name,
            args.kind,
            transport,
            visibility=visibility,
        )
        print(
            f"Registered MCP endpoint {endpoint.name!r} "
            f"(kind={endpoint.kind}, mode={endpoint.mode}, "
            f"visibility={endpoint.visibility})"
        )
        print("mcp_servers config block:")
        print(json.dumps({endpoint.name: endpoint.to_server_config()}, indent=2))
        return

    # list (default)
    endpoints = await registry.list_for_principal(principal)
    if not endpoints:
        print(f"No MCP endpoints visible to {principal.user_id!r} in mode {mode}.")
        return
    print(f"MCP endpoints visible to {principal.user_id!r} (mode={mode}):")
    for endpoint in endpoints:
        print(
            f"  - {endpoint.name}  [{endpoint.kind}]  "
            f"visibility={endpoint.visibility}  "
            f"transport={endpoint.transport.get('type')}"
        )


def cmd_endpoints(args) -> None:
    """Dispatch ``hermes mcp endpoints`` (``list`` default, ``register``)."""
    action = getattr(args, "endpoints_action", None) or "list"
    asyncio.run(_run(args, action))
