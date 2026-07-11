"""Mode-aware MCP endpoint registry (FG-11 — agent comms, client side).

This is the uniform registration path for the two client-side MCP directions
Hermes drives: **in-house** tool MCPs (FG-07) and **remote** system MCPs
(FG-08). Both land as rows in one ``mcp_endpoints`` registry, so FG-07/FG-08
share a single seam instead of each inventing its own store.

The registry consumes the already-merged Wave-0 contracts — it does not
re-implement them:

* **C3 datastore routing** — every connection is obtained through
  :class:`hermes_cli.datastore.SupabaseAppStore`, whose ``mode`` selects the
  ``app_dev`` / ``app_prod`` schema. Because the table lives *inside* the
  mode's schema, a ``dev`` endpoint is written into ``app_dev`` and is only
  reachable from a dev session; a channel-forced ``prod`` session (C3 forces
  channels to prod) never sees it. The row also carries an explicit ``mode``
  column so a materialized config block is self-describing.
* **C2 visibility scoping** — every row carries ``owner_user_id`` +
  ``visibility`` (``shared`` or ``private:<user_id>``). Reads are filtered by
  :func:`hermes_cli.access.scope_filter`, and Postgres row-level security
  (:func:`hermes_cli.access.apply_scope_rls`) is the database-level backstop.
  The owner role bypasses scoping and sees every endpoint.
* **C1 principals** — registration and resolution are performed *as* a
  :class:`hermes_cli.access.Principal`; a ``viewer`` may not register.

**Cache-safety (AGENTS.md).** Resolving the endpoints a principal may use
returns the ``mcp_servers.<name>`` config blocks that a **future** session's
MCP client (:mod:`tools.mcp_tool`) should connect to via
:func:`tools.mcp_tool.register_mcp_servers`. Nothing in this module mutates a
live conversation's toolset: a newly-registered endpoint becomes available to
the *next* session that resolves the registry, never spliced into a running
conversation's cached prompt/tool schema.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Literal, Mapping, Optional

from hermes_cli.access import (
    Principal,
    SHARED,
    apply_scope_rls,
    normalize_visibility,
    scope_filter,
)

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.datastore import SupabaseAppStore


#: Registry table (RLS applied). Lives in the store's mode schema (C3).
ENDPOINTS_TABLE = "mcp_endpoints"

EndpointKind = Literal["in_house", "remote"]
_ENDPOINT_KINDS: tuple[EndpointKind, ...] = ("in_house", "remote")

TransportType = Literal["stdio", "http"]

_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {ENDPOINTS_TABLE} (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('in_house', 'remote')),
    transport JSONB NOT NULL,
    owner_user_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('dev', 'prod')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS {ENDPOINTS_TABLE}_visibility_idx
    ON {ENDPOINTS_TABLE} (visibility);
"""


def _validate_transport(transport: Mapping[str, object]) -> dict:
    """Validate a transport spec and return a normalized copy.

    Accepts the same two transports the MCP client (:mod:`tools.mcp_tool`)
    understands: ``stdio`` (needs ``command``, optional ``args`` / ``env``)
    and ``http`` (needs ``url``, optional ``auth``). Raises ``ValueError`` on
    anything malformed so a bad row never reaches the store.
    """
    if not isinstance(transport, Mapping):
        raise ValueError("transport must be a mapping")
    ttype = transport.get("type")
    if ttype not in ("stdio", "http"):
        raise ValueError(f"transport.type must be 'stdio' or 'http', got {ttype!r}")

    normalized: dict = {"type": ttype}
    if ttype == "stdio":
        command = transport.get("command")
        if not command or not isinstance(command, str):
            raise ValueError("stdio transport requires a non-empty 'command'")
        normalized["command"] = command
        args = transport.get("args")
        if args is not None:
            if not isinstance(args, (list, tuple)) or not all(
                isinstance(a, str) for a in args
            ):
                raise ValueError("transport.args must be a list of strings")
            normalized["args"] = [str(a) for a in args]
        env = transport.get("env")
        if env is not None:
            if not isinstance(env, Mapping) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in env.items()
            ):
                raise ValueError("transport.env must be a mapping of str -> str")
            normalized["env"] = {str(k): str(v) for k, v in env.items()}
    else:  # http
        url = transport.get("url")
        if not url or not isinstance(url, str):
            raise ValueError("http transport requires a non-empty 'url'")
        normalized["url"] = url
        auth = transport.get("auth")
        if auth is not None:
            if not isinstance(auth, str):
                raise ValueError("transport.auth must be a string")
            normalized["auth"] = auth
    return normalized


@dataclass(frozen=True)
class MCPEndpoint:
    """One registered MCP server (in-house or remote), scoped + mode-tagged."""

    id: str
    name: str
    kind: EndpointKind
    transport: dict
    owner_user_id: str
    visibility: str
    mode: str

    def to_server_config(self) -> dict:
        """Translate to the ``mcp_servers.<name>`` block the client consumes.

        Produces the exact shape :func:`tools.mcp_tool.register_mcp_servers`
        (and ``hermes mcp add``) expect, so a registered endpoint plugs into
        the existing MCP client with zero new client code.
        """
        transport = self.transport
        ttype = transport.get("type")
        cfg: dict = {}
        if ttype == "stdio":
            cfg["command"] = transport.get("command", "")
            if transport.get("args"):
                cfg["args"] = list(transport["args"])
            if transport.get("env"):
                cfg["env"] = dict(transport["env"])
        elif ttype == "http":
            cfg["url"] = transport.get("url", "")
            if transport.get("auth"):
                cfg["auth"] = transport["auth"]
        else:  # pragma: no cover - guarded at registration time
            raise ValueError(f"Unknown transport type: {ttype!r}")
        return cfg

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "transport": dict(self.transport),
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "mode": self.mode,
        }


def _row_to_endpoint(row: "asyncpg.Record") -> MCPEndpoint:
    transport = row["transport"]
    if isinstance(transport, str):
        transport = json.loads(transport)
    return MCPEndpoint(
        id=str(row["id"]),
        name=str(row["name"]),
        kind=str(row["kind"]),  # type: ignore[arg-type]
        transport=dict(transport),
        owner_user_id=str(row["owner_user_id"]),
        visibility=str(row["visibility"]),
        mode=str(row["mode"]),
    )


def _resolve_visibility(principal: Principal, visibility: Optional[str]) -> str:
    """Map a requested visibility intent onto a concrete C2 tag.

    ``None`` / ``"private"`` becomes the caller's own ``private:<user_id>`` — a
    principal can only register an endpoint private to *itself*. ``"shared"``
    or a fully-qualified ``private:<u>`` is validated and passed through, but a
    non-owner may not register a row private to *another* user.
    """
    if visibility is None or visibility == "private":
        return principal.private_visibility
    resolved = normalize_visibility(visibility)
    if resolved != SHARED and not principal.is_owner:
        if resolved != principal.private_visibility:
            raise PermissionError(
                "A non-owner may only register 'shared' or its own "
                "'private' endpoints"
            )
    return resolved


class MCPEndpointRegistry:
    """Async CRUD over the C2-scoped, C3-routed ``mcp_endpoints`` table.

    The registry never opens a raw connection — it always routes through the
    injected contract-C3 :class:`SupabaseAppStore`, whose ``mode`` selects the
    ``app_dev`` / ``app_prod`` schema (and therefore which endpoints exist).
    """

    def __init__(self, store: "SupabaseAppStore") -> None:
        self._store = store

    @property
    def mode(self) -> str:
        return self._store.mode

    async def _connect(self) -> "asyncpg.Connection":
        connection = await self._store.connect()
        await connection.execute(
            f'CREATE SCHEMA IF NOT EXISTS "{self._store.schema}"'
        )
        return connection

    async def initialize(
        self,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        """Create the registry table + its RLS policy. Idempotent."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await conn.execute(_SCHEMA_SQL)
            await apply_scope_rls(conn, ENDPOINTS_TABLE)
        finally:
            if own:
                await conn.close()

    async def register(
        self,
        principal: Principal,
        name: str,
        kind: EndpointKind,
        transport: Mapping[str, object],
        *,
        visibility: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> MCPEndpoint:
        """Register (or update) an MCP endpoint as ``principal``.

        A ``viewer`` may not register (unauthenticated MCP peers resolve to
        ``viewer`` — they consume the surface, they do not extend it). The
        endpoint's ``mode`` is the store's mode, so it lands in the matching
        schema and only future sessions of that mode resolve it.
        """
        if principal.role == "viewer":
            raise PermissionError("viewer principals may not register MCP endpoints")
        if not _VALID_NAME.fullmatch(name or ""):
            raise ValueError(f"Invalid endpoint name: {name!r}")
        if kind not in _ENDPOINT_KINDS:
            raise ValueError(f"Invalid endpoint kind: {kind!r}")
        normalized_transport = _validate_transport(transport)
        resolved_visibility = _resolve_visibility(principal, visibility)

        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {ENDPOINTS_TABLE}
                    (id, name, kind, transport, owner_user_id, visibility, mode)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
                ON CONFLICT (name) DO UPDATE SET
                    kind = EXCLUDED.kind,
                    transport = EXCLUDED.transport,
                    visibility = EXCLUDED.visibility
                WHERE {ENDPOINTS_TABLE}.owner_user_id = EXCLUDED.owner_user_id
                RETURNING id, name, kind, transport, owner_user_id,
                          visibility, mode
                """,
                f"mep_{uuid.uuid4().hex}",
                name,
                kind,
                json.dumps(normalized_transport, sort_keys=True),
                principal.user_id,
                resolved_visibility,
                self._store.mode,
            )
            if row is None:
                # The name exists but is owned by someone else — the conflict
                # UPDATE's WHERE filtered it out. Do not leak the other owner.
                raise PermissionError(
                    f"Endpoint {name!r} is owned by another principal"
                )
            return _row_to_endpoint(row)
        finally:
            if own:
                await conn.close()

    async def get(
        self,
        name: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[MCPEndpoint]:
        """Return the endpoint named ``name`` in this mode, else ``None``.

        This is an unscoped lookup by primary key; callers that must honour C2
        should use :meth:`list_for_principal` / :meth:`resolve_server_configs`.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                SELECT id, name, kind, transport, owner_user_id, visibility, mode
                FROM {ENDPOINTS_TABLE} WHERE name = $1
                """,
                name,
            )
            return _row_to_endpoint(row) if row is not None else None
        finally:
            if own:
                await conn.close()

    async def list_for_principal(
        self,
        principal: Principal,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[MCPEndpoint]:
        """Return every endpoint ``principal`` may use (contract C2).

        A non-owner sees ``shared`` endpoints plus its own ``private`` ones;
        the owner sees all. Because the table lives in the store's mode schema,
        the result is already mode-scoped (dev endpoints in dev, prod in prod).
        """
        predicate = scope_filter(principal, start_index=1)
        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT id, name, kind, transport, owner_user_id, visibility, mode
                FROM {ENDPOINTS_TABLE}
                WHERE {predicate.sql}
                ORDER BY name
                """,
                *predicate.params,
            )
            return [_row_to_endpoint(row) for row in rows]
        finally:
            if own:
                await conn.close()

    async def resolve_server_configs(
        self,
        principal: Principal,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Dict[str, dict]:
        """Return ``{name: mcp_servers-config}`` for every endpoint in scope.

        This is the **future-sessions-only** materialization: the returned dict
        is meant to be merged into a *new* session's MCP client config (fed to
        :func:`tools.mcp_tool.register_mcp_servers` at session start). It must
        never be applied to a live conversation — doing so would splice tools
        into a cached prompt/tool schema and break prompt-cache safety.
        """
        endpoints = await self.list_for_principal(principal, connection=connection)
        return {ep.name: ep.to_server_config() for ep in endpoints}


__all__ = [
    "ENDPOINTS_TABLE",
    "EndpointKind",
    "MCPEndpoint",
    "MCPEndpointRegistry",
]
