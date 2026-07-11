"""Provenance registry for acquired OSS systems (FG-08).

Every capability the agent *acquires from open source* — whether a **remote
system** (an OSS project cloned + hosted on a different machine and fronted by
an MCP) or an **in-house rebuild** — records one provenance row so the fleet
always knows *where a capability came from*, *at which pinned commit*, *under
which license*, and *on which host* it runs. This is the ``provenance(tool_id,
repo_url, license, commit, host, vetted_at)`` record the FG-08 design calls for.

Like the FG-07 tool registry and the FG-11 endpoint registry, this module only
*consumes* the already-merged Wave-0 contracts — it never re-implements them:

* **C3 datastore routing.** Every connection is obtained through the injected
  :class:`hermes_cli.datastore.SupabaseAppStore`, whose ``mode`` selects the
  ``app_dev`` / ``app_prod`` schema. A provenance row authored in ``dev`` lives
  in ``app_dev`` and is only visible from a dev session; the row also carries an
  explicit ``mode`` column so a materialized record is self-describing.
* **C2 visibility scoping.** Every row carries ``owner_user_id`` +
  ``visibility`` (``shared`` or ``private:<user_id>``). Reads are filtered by
  :func:`hermes_cli.access.scope_filter`; Postgres row-level security
  (:func:`hermes_cli.access.apply_scope_rls`) is the database-level backstop.
  The owner role bypasses scoping and sees every provenance row.
* **C1 principals.** Recording happens *as* a
  :class:`hermes_cli.access.Principal`; a ``viewer`` may not record provenance.

**Cache-safety (AGENTS.md).** Nothing here touches a live conversation's system
prompt or toolset — a provenance row is pure audit metadata read on demand.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, List, Literal, Optional

from hermes_cli.access import (
    SHARED,
    Principal,
    apply_scope_rls,
    normalize_visibility,
    scope_filter,
)

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.datastore import SupabaseAppStore


#: Provenance table (RLS applied). Lives in the store's mode schema (C3).
PROVENANCE_TABLE = "provenance"

#: Where the capability came from: an OSS clone hosted off-box, or an in-house
#: rebuild. Both flow through the same acquisition surface (FG-08).
ProvenanceSource = Literal["remote", "in_house"]
_SOURCES: tuple[ProvenanceSource, ...] = ("remote", "in_house")

_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {PROVENANCE_TABLE} (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL CHECK (source IN ('remote', 'in_house')),
    repo_url TEXT NOT NULL DEFAULT '',
    license TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL DEFAULT '',
    owner_user_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('dev', 'prod')),
    vetted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS {PROVENANCE_TABLE}_visibility_idx
    ON {PROVENANCE_TABLE} (visibility);
"""

_COLUMNS = (
    "id, tool_name, source, repo_url, license, commit_sha, host, "
    "owner_user_id, visibility, mode, vetted_at"
)


@dataclass(frozen=True)
class Provenance:
    """One acquired-capability provenance record, scoped + mode-tagged."""

    id: str
    tool_name: str
    source: ProvenanceSource
    repo_url: str
    license: str
    commit_sha: str
    host: str
    owner_user_id: str
    visibility: str
    mode: str
    vetted_at: Optional[datetime]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "source": self.source,
            "repo_url": self.repo_url,
            "license": self.license,
            "commit_sha": self.commit_sha,
            "host": self.host,
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "mode": self.mode,
            "vetted_at": self.vetted_at.isoformat() if self.vetted_at else None,
        }


def _row_to_provenance(row: "asyncpg.Record") -> Provenance:
    return Provenance(
        id=str(row["id"]),
        tool_name=str(row["tool_name"]),
        source=str(row["source"]),  # type: ignore[arg-type]
        repo_url=str(row["repo_url"]),
        license=str(row["license"]),
        commit_sha=str(row["commit_sha"]),
        host=str(row["host"]),
        owner_user_id=str(row["owner_user_id"]),
        visibility=str(row["visibility"]),
        mode=str(row["mode"]),
        vetted_at=row["vetted_at"],
    )


def _resolve_visibility(principal: Principal, visibility: Optional[str]) -> str:
    """Map a requested visibility intent onto a concrete C2 tag.

    ``None`` / ``"private"`` becomes the caller's own ``private:<user_id>`` — a
    principal can only record provenance private to *itself*. ``"shared"`` or a
    fully-qualified ``private:<u>`` is validated and passed through, but a
    non-owner may not record a row private to *another* user.
    """
    if visibility is None or visibility == "private":
        return principal.private_visibility
    resolved = normalize_visibility(visibility)
    if resolved != SHARED and not principal.is_owner:
        if resolved != principal.private_visibility:
            raise PermissionError(
                "A non-owner may only record 'shared' or its own 'private' "
                "provenance"
            )
    return resolved


class ProvenanceRegistry:
    """Async CRUD over the C2-scoped, C3-routed ``provenance`` table."""

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
        """Create the provenance table + its RLS policy. Idempotent."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await conn.execute(_SCHEMA_SQL)
            await apply_scope_rls(conn, PROVENANCE_TABLE)
        finally:
            if own:
                await conn.close()

    async def record(
        self,
        principal: Principal,
        tool_name: str,
        source: ProvenanceSource,
        *,
        repo_url: str = "",
        license: str = "",
        commit_sha: str = "",
        host: str = "",
        visibility: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Provenance:
        """Record (or update) a provenance row as ``principal``.

        A ``viewer`` may not record provenance. The row's ``mode`` is the
        store's mode, so it lands in the matching schema and only sessions of
        that mode resolve it.
        """
        if principal.role == "viewer":
            raise PermissionError("viewer principals may not record provenance")
        if not _VALID_NAME.fullmatch(tool_name or ""):
            raise ValueError(f"Invalid tool name: {tool_name!r}")
        if source not in _SOURCES:
            raise ValueError(f"Invalid provenance source: {source!r}")
        resolved_visibility = _resolve_visibility(principal, visibility)

        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {PROVENANCE_TABLE}
                    (id, tool_name, source, repo_url, license, commit_sha,
                     host, owner_user_id, visibility, mode)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (tool_name) DO UPDATE SET
                    source = EXCLUDED.source,
                    repo_url = EXCLUDED.repo_url,
                    license = EXCLUDED.license,
                    commit_sha = EXCLUDED.commit_sha,
                    host = EXCLUDED.host,
                    visibility = EXCLUDED.visibility,
                    vetted_at = NOW()
                WHERE {PROVENANCE_TABLE}.owner_user_id = EXCLUDED.owner_user_id
                RETURNING {_COLUMNS}
                """,
                f"prv_{uuid.uuid4().hex}",
                tool_name,
                source,
                repo_url,
                license,
                commit_sha,
                host,
                principal.user_id,
                resolved_visibility,
                self._store.mode,
            )
            if row is None:
                # The tool_name exists but is owned by someone else — the
                # conflict UPDATE's WHERE filtered it out. Do not leak the owner.
                raise PermissionError(
                    f"Provenance for {tool_name!r} is owned by another principal"
                )
            return _row_to_provenance(row)
        finally:
            if own:
                await conn.close()

    async def get(
        self,
        tool_name: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[Provenance]:
        """Return the provenance row for ``tool_name`` in this mode, else None."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM {PROVENANCE_TABLE} WHERE tool_name = $1",
                tool_name,
            )
            return _row_to_provenance(row) if row is not None else None
        finally:
            if own:
                await conn.close()

    async def list_for_principal(
        self,
        principal: Principal,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[Provenance]:
        """Return every provenance row ``principal`` may see (contract C2)."""
        predicate = scope_filter(principal, start_index=1)
        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT {_COLUMNS} FROM {PROVENANCE_TABLE}
                WHERE {predicate.sql}
                ORDER BY tool_name
                """,
                *predicate.params,
            )
            return [_row_to_provenance(row) for row in rows]
        finally:
            if own:
                await conn.close()


__all__ = [
    "PROVENANCE_TABLE",
    "ProvenanceSource",
    "Provenance",
    "ProvenanceRegistry",
]
