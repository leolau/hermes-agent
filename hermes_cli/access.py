"""Multi-user access model for the single shared Hermes brain (contracts C1, C2).

This module publishes the two Wave-0 access contracts consumed by later
feature groups (memory, skills, goals, tasks, tools, assets):

* **C1 — principal/identity.** :class:`Principal` binds a system ``user_id`` to
  a single :data:`Role` (``owner`` | ``admin`` | ``member`` | ``viewer``) plus
  the channel identities that map to it. :func:`resolve_principal` is the
  gateway seam that turns an inbound channel identity into a principal, reusing
  ``gateway/pairing.py`` for enrolment. Identities are backed by self-hosted
  Supabase: ``principals.user_id`` **is** the GoTrue subject id, and the
  ``channel_identities`` table maps ``(platform, channel_user_id)`` onto it.

* **C2 — visibility/scoping.** Every scoped row carries ``owner_user_id`` and a
  ``visibility`` of either :data:`SHARED` (readable by all members) or
  ``private:<user_id>`` (readable only by that user). :func:`can_read` and
  :func:`scope_filter` are the app-layer filter; :func:`apply_scope_rls`
  installs the equivalent **Postgres row-level security** so the boundary is
  enforced at the database and cannot be bypassed from the app layer. The owner
  role bypasses the filter and sees everything.

  **Per-item grants (FG-19).** On top of shared/private, a row may be
  *granted* to specific users through the :data:`ITEM_GRANTS_TABLE` — a
  cross-user assignment that does **not** downgrade the row's visibility (the
  owner's *other* private rows stay hidden). :func:`scope_filter` and
  :func:`apply_scope_rls` take an optional ``grant_item_kind`` so a
  grant-scoped table (a GTS goal/task) additionally reads a row when an active
  (``pending``/``accepted``) grant to the requesting principal exists for that
  exact item. This is a narrow extension of the existing C2 helpers, not a
  second access system.

Datastore routing always goes through contract C3
(:func:`hermes_cli.datastore.get_store`) — this module never opens a raw
connection or re-implements mode routing.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Literal, Mapping, Protocol

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.datastore import SupabaseAppStore


# ---------------------------------------------------------------------------
# C1 — principal / identity model
# ---------------------------------------------------------------------------

Role = Literal["owner", "admin", "member", "viewer"]

ROLES: tuple[Role, ...] = ("owner", "admin", "member", "viewer")

#: Roles that may read every private tier (the owner bypasses scope filtering).
_OWNER_ROLE: Role = "owner"

SHARED: Literal["shared"] = "shared"
_PRIVATE_PREFIX = "private:"

_VALID_USER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ChannelOrigin(Protocol):
    """Minimal inbound-source contract needed to resolve a principal."""

    @property
    def platform(self) -> object:
        """Return the origin platform (an enum exposing ``value``, or a str)."""
        ...

    @property
    def user_id(self) -> str | None:
        """Return the channel-native user identifier, if any."""
        ...


@dataclass(frozen=True)
class Principal:
    """A resolved system user and its single role (contract C1).

    ``user_id`` is the stable system identity — the Supabase GoTrue subject id.
    ``channels`` lists the ``platform:channel_user_id`` identities that map onto
    this principal. Exactly one principal in the shared brain may hold the
    ``owner`` role at a time (enforced by a partial unique index and by the
    approval-gated transfer flow).
    """

    user_id: str
    display: str
    role: Role
    channels: tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"Unknown role: {self.role!r}")
        if not self.user_id or not self.user_id.strip():
            raise ValueError("Principal.user_id cannot be empty")

    @property
    def is_owner(self) -> bool:
        """Whether this principal holds the single owner role (bypasses scope)."""
        return self.role == _OWNER_ROLE

    @property
    def private_visibility(self) -> str:
        """The ``private:<user_id>`` tag for rows only this principal may read."""
        return private(self.user_id)


def private(user_id: str) -> str:
    """Return the ``private:<user_id>`` visibility tag for a user."""
    if not user_id or not user_id.strip():
        raise ValueError("private() requires a non-empty user_id")
    return f"{_PRIVATE_PREFIX}{user_id}"


def parse_private_owner(visibility: str) -> str | None:
    """Return the user id embedded in a ``private:<user_id>`` tag, else ``None``."""
    if isinstance(visibility, str) and visibility.startswith(_PRIVATE_PREFIX):
        owner = visibility[len(_PRIVATE_PREFIX):]
        return owner or None
    return None


def normalize_visibility(visibility: str) -> str:
    """Validate and normalize a visibility tag (``shared`` or ``private:<u>``)."""
    if visibility == SHARED:
        return SHARED
    owner = parse_private_owner(visibility)
    if owner is None:
        raise ValueError(
            f"Invalid visibility {visibility!r}; expected 'shared' or "
            "'private:<user_id>'"
        )
    return private(owner)


# ---------------------------------------------------------------------------
# C2 — per-item grants (FG-19): cross-user assignment as a per-row grant
# ---------------------------------------------------------------------------

#: The per-item grant table (FG-19). A grant is a cross-user share of one
#: specific GTS item — an ``assignee`` (single, may act) or a read-only
#: ``watcher`` — layered on top of the shared/private ``visibility`` tag. It
#: never rewrites ``visibility``, so the owner's *other* private rows stay
#: hidden from the grantee.
ITEM_GRANTS_TABLE = "item_grants"

#: Kinds of GTS node a grant may target (matches the C9 graph).
GRANT_ITEM_KINDS: tuple[str, ...] = ("goal", "task")
#: Grant roles: a single ``assignee`` (may advance progress) + read-only
#: ``watcher``s.
GRANT_TYPES: tuple[str, ...] = ("assignee", "watcher")
#: Grant lifecycle states. Only :data:`GRANT_ACTIVE_STATUSES` confer access.
GRANT_STATUSES: tuple[str, ...] = ("pending", "accepted", "declined", "revoked")
#: The statuses that actually confer read/act access (a declined/revoked grant
#: confers nothing).
GRANT_ACTIVE_STATUSES: tuple[str, ...] = ("pending", "accepted")

ITEM_GRANTS_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {ITEM_GRANTS_TABLE} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_kind TEXT NOT NULL CHECK (item_kind IN ('goal', 'task')),
    item_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    grant_type TEXT NOT NULL CHECK (grant_type IN ('assignee', 'watcher')),
    granted_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted'
        CHECK (status IN ('pending', 'accepted', 'declined', 'revoked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (item_kind, item_id, user_id)
);
CREATE INDEX IF NOT EXISTS {ITEM_GRANTS_TABLE}_grantee_idx
    ON {ITEM_GRANTS_TABLE} (user_id, status);
CREATE INDEX IF NOT EXISTS {ITEM_GRANTS_TABLE}_item_idx
    ON {ITEM_GRANTS_TABLE} (item_kind, item_id);
-- The single-assignee invariant (D15): at most one *active* assignee per item.
CREATE UNIQUE INDEX IF NOT EXISTS {ITEM_GRANTS_TABLE}_single_assignee
    ON {ITEM_GRANTS_TABLE} (item_kind, item_id)
    WHERE grant_type = 'assignee' AND status IN ('pending', 'accepted');
"""


def _grant_exists_sql(item_kind: str, id_expr: str, user_expr: str) -> str:
    """SQL ``EXISTS`` clause: an active grant of ``item_kind`` to ``user_expr``.

    ``item_kind`` is validated against :data:`GRANT_ITEM_KINDS` and inlined
    (it is a fixed enum, never user input); ``id_expr`` / ``user_expr`` are
    caller-controlled SQL fragments (a validated column reference and either a
    ``$n`` placeholder or a ``current_setting(...)`` call).
    """
    if item_kind not in GRANT_ITEM_KINDS:
        raise ValueError(f"Unknown grant item_kind: {item_kind!r}")
    statuses = ", ".join(f"'{status}'" for status in GRANT_ACTIVE_STATUSES)
    return (
        f"EXISTS (SELECT 1 FROM {ITEM_GRANTS_TABLE} ig "
        f"WHERE ig.item_kind = '{item_kind}' AND ig.item_id = {id_expr} "
        f"AND ig.user_id = {user_expr} AND ig.status IN ({statuses}))"
    )


# ---------------------------------------------------------------------------
# C2 — visibility / scoping helpers (app-layer filter)
# ---------------------------------------------------------------------------


def can_read(
    principal: Principal,
    visibility: str,
    *,
    granted: bool = False,
) -> bool:
    """Whether ``principal`` may read a row with the given ``visibility``.

    The owner role bypasses the filter (sees everything). ``shared`` rows are
    readable by every member; a ``private:<u>`` row is readable only by ``u``.
    ``granted`` (FG-19) is set by the caller when an active per-item grant to
    ``principal`` exists for the row — a grant confers read access to *that*
    item without touching its ``visibility`` tag.
    """
    if principal.is_owner:
        return True
    if visibility == SHARED:
        return True
    if granted:
        return True
    owner = parse_private_owner(visibility)
    return owner is not None and owner == principal.user_id


def can_read_row(
    principal: Principal,
    row: Mapping[str, object],
    *,
    granted: bool = False,
) -> bool:
    """Convenience wrapper of :func:`can_read` for a row mapping.

    Reads the ``visibility`` key; a missing/empty value is treated as an
    unreadable private-to-nobody row (fail closed) unless the caller is owner
    or holds an active per-item grant (``granted``, FG-19).
    """
    if principal.is_owner or granted:
        return True
    visibility = row.get("visibility")
    if not isinstance(visibility, str) or not visibility:
        return False
    return can_read(principal, visibility)


@dataclass(frozen=True)
class ScopePredicate:
    """A SQL read-visibility predicate + positional params for asyncpg.

    ``sql`` slots into a ``WHERE`` clause; ``params`` are the ``$n`` bind
    values in order. ``start_index`` controls the first placeholder number so
    the predicate composes with a caller's existing parameters.
    """

    sql: str
    params: tuple[str, ...]


def scope_filter(
    principal: Principal,
    *,
    column: str = "visibility",
    start_index: int = 1,
    grant_item_kind: str | None = None,
    id_column: str = "id",
) -> ScopePredicate:
    """Return the read-visibility predicate for ``principal`` (contract C2).

    The owner role bypasses scoping (``TRUE`` with no params). A non-owner sees
    ``shared`` rows plus its own ``private:<user_id>`` rows. The predicate is
    parameterized to keep it injection-safe when composed into an asyncpg query.

    When ``grant_item_kind`` (FG-19) is given, the predicate also matches a row
    whose ``id_column`` has an active per-item grant to ``principal`` — the
    "assigned/granted to me" clause. This never widens access to the owner's
    *other* private rows: the grant is correlated to the row's own id. The
    grant clause binds one extra positional param (the principal's user id) at
    ``start_index + 1``, so a caller composing further placeholders must offset
    by 2 rather than 1.
    """
    if not _VALID_COLUMN.fullmatch(column):
        raise ValueError(f"Invalid column name for scope_filter: {column!r}")
    if principal.is_owner:
        return ScopePredicate("TRUE", ())
    placeholder = f"${start_index}"
    clauses = [f"{column} = 'shared'", f"{column} = {placeholder}"]
    params: tuple[str, ...] = (principal.private_visibility,)
    if grant_item_kind is not None:
        if not _VALID_COLUMN.fullmatch(id_column):
            raise ValueError(f"Invalid id_column for scope_filter: {id_column!r}")
        grant_placeholder = f"${start_index + 1}"
        clauses.append(
            _grant_exists_sql(grant_item_kind, id_column, grant_placeholder)
        )
        params = (principal.private_visibility, principal.user_id)
    sql = "(" + " OR ".join(clauses) + ")"
    return ScopePredicate(sql, params)


_VALID_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


# ---------------------------------------------------------------------------
# Database schema (Supabase app layer) — principals, identities, RLS
# ---------------------------------------------------------------------------

# Row-level security keys the read decision off two GUCs that mirror the JWT
# claims PostgREST/GoTrue would expose (``request.jwt.claims``): the requesting
# principal id and role. On the deployed stack these come from the verified
# access token; in direct-asyncpg tests they are set via :func:`bind_principal`.
_GUC_ID = "hermes.principal_id"
_GUC_ROLE = "hermes.principal_role"

ACCESS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS principals (
    user_id TEXT PRIMARY KEY,
    display TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Exactly-one-owner invariant: at most one principal may hold the owner role.
CREATE UNIQUE INDEX IF NOT EXISTS principals_single_owner
    ON principals (role) WHERE role = 'owner';

CREATE TABLE IF NOT EXISTS channel_identities (
    platform TEXT NOT NULL,
    channel_user_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES principals(user_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (platform, channel_user_id)
);
CREATE INDEX IF NOT EXISTS channel_identities_user
    ON channel_identities (user_id);
"""


async def initialize_access(connection: asyncpg.Connection) -> None:
    """Create the C1 principal/identity tables in the connection's schema.

    Idempotent. The connection's ``search_path`` selects the ``app_dev`` or
    ``app_prod`` schema (contract C3), so the same DDL yields schema parity
    across dev and prod as FG-01 requires.
    """
    await connection.execute(ACCESS_SCHEMA_SQL)


async def apply_scope_rls(
    connection: asyncpg.Connection,
    table: str,
    *,
    grant_item_kind: str | None = None,
    id_column: str = "id",
) -> None:
    """Enforce contract-C2 visibility on ``table`` via Postgres RLS.

    ``table`` must carry ``owner_user_id`` and ``visibility`` columns. Installs
    a ``FORCE``d row-level-security read policy so that — even for the table
    owner — a session sees a row only when the bound principal is the owner
    role, the row is ``shared``, or the row is that principal's own
    ``private:<user_id>``. This is the database-level mirror of
    :func:`scope_filter`; the app-layer filter is defense in depth on top.

    When ``grant_item_kind`` (FG-19) is given, the policy gains the
    database-level "granted to me" clause: a row of ``table`` is also visible
    when an active per-item grant to the bound principal exists for that exact
    ``id_column`` — the Postgres mirror of :func:`scope_filter`'s grant clause.
    Re-invoking this (the grant-aware call replaces the plain policy) is safe
    because the policy is dropped and recreated.
    """
    if not _VALID_COLUMN.fullmatch(table):
        raise ValueError(f"Invalid table name: {table!r}")
    grant_clause = ""
    if grant_item_kind is not None:
        if not _VALID_COLUMN.fullmatch(id_column):
            raise ValueError(f"Invalid id_column: {id_column!r}")
        grant_clause = "\n                OR " + _grant_exists_sql(
            grant_item_kind,
            f"{table}.{id_column}",
            f"current_setting('{_GUC_ID}', true)",
        )
    await connection.execute(
        f"""
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS hermes_scope_read ON {table};
        CREATE POLICY hermes_scope_read ON {table}
            FOR SELECT
            USING (
                current_setting('{_GUC_ROLE}', true) = 'owner'
                OR visibility = 'shared'
                OR visibility = 'private:' || current_setting('{_GUC_ID}', true){grant_clause}
            );
        """
    )


async def apply_item_grants_rls(connection: asyncpg.Connection) -> None:
    """Enforce read RLS on the FG-19 :data:`ITEM_GRANTS_TABLE`.

    A ``FORCE``d read policy so a session sees a grant row only when it is the
    owner role, the grantee (``user_id``), or the granter (``granted_by``).
    This keeps the grant ledger itself scoped, and — because the goal/task
    read policies reference this table in a correlated sub-select — it lets a
    grantee's own grant satisfy those policies without exposing anyone else's
    grants.
    """
    await connection.execute(
        f"""
        ALTER TABLE {ITEM_GRANTS_TABLE} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {ITEM_GRANTS_TABLE} FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS hermes_grant_read ON {ITEM_GRANTS_TABLE};
        CREATE POLICY hermes_grant_read ON {ITEM_GRANTS_TABLE}
            FOR SELECT
            USING (
                current_setting('{_GUC_ROLE}', true) = 'owner'
                OR user_id = current_setting('{_GUC_ID}', true)
                OR granted_by = current_setting('{_GUC_ID}', true)
            );
        """
    )


async def bind_principal(
    connection: asyncpg.Connection,
    principal: Principal,
) -> None:
    """Bind ``principal`` to the connection for the length of the transaction.

    Sets the ``hermes.principal_id`` / ``hermes.principal_role`` GUCs the RLS
    policy reads. Uses ``set_config(..., is_local => true)`` so the binding is
    scoped to the current transaction, mirroring how a per-request JWT scopes
    ``request.jwt.claims`` on the deployed PostgREST/GoTrue stack.
    """
    await connection.execute(
        "SELECT set_config($1, $2, true), set_config($3, $4, true)",
        _GUC_ID,
        principal.user_id,
        _GUC_ROLE,
        principal.role,
    )


# ---------------------------------------------------------------------------
# Principal store (C1 persistence + owner transfer)
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_user_id(user_id: str) -> str:
    user_id = (user_id or "").strip()
    if not _VALID_USER_ID.fullmatch(user_id):
        raise ValueError(f"Invalid user_id: {user_id!r}")
    return user_id


def _row_to_principal(
    row: Mapping[str, object],
    channels: tuple[str, ...] = (),
) -> Principal:
    return Principal(
        user_id=str(row["user_id"]),
        display=str(row["display"] or ""),
        role=_coerce_role(row["role"]),
        channels=channels,
        created_at=_coerce_dt(row.get("created_at")),
    )


def _coerce_role(value: object) -> Role:
    if value in ROLES:
        return value  # type: ignore[return-value]
    raise ValueError(f"Unknown role loaded from store: {value!r}")


def _coerce_dt(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


@dataclass(frozen=True)
class TransferResult:
    """References emitted by one successful ownership transfer."""

    from_user_id: str
    to_user_id: str
    approval_ref: str
    change_ref: str


class PrincipalStore:
    """Async CRUD + owner-transfer over the Supabase ``principals`` table.

    Every method routes through the contract-C3 :class:`SupabaseAppStore`; the
    store's ``mode`` selects the ``app_dev`` / ``app_prod`` schema. Ownership
    lives in prod (auth is prod), so :meth:`transfer_owner` requires a prod
    store and records its approval + change-event there.
    """

    def __init__(self, store: SupabaseAppStore) -> None:
        self._store = store

    @property
    def mode(self) -> str:
        return self._store.mode

    async def _channels_for(
        self,
        connection: asyncpg.Connection,
        user_id: str,
    ) -> tuple[str, ...]:
        rows = await connection.fetch(
            """
            SELECT platform, channel_user_id
            FROM channel_identities
            WHERE user_id = $1
            ORDER BY platform, channel_user_id
            """,
            user_id,
        )
        return tuple(f"{r['platform']}:{r['channel_user_id']}" for r in rows)

    async def enroll(
        self,
        user_id: str,
        *,
        display: str = "",
        role: Role = "member",
        connection: asyncpg.Connection | None = None,
    ) -> Principal:
        """Create (or return) a principal. New users default to ``member``.

        Enrolling the very first principal as ``owner`` bootstraps the single
        owner; a second ``owner`` enrolment raises via the partial unique index.
        """
        user_id = _validate_user_id(user_id)
        if role not in ROLES:
            raise ValueError(f"Unknown role: {role!r}")

        own_connection = connection is None
        conn = connection or await self._store.connect()
        try:
            await initialize_access(conn)
            row = await conn.fetchrow(
                """
                INSERT INTO principals (user_id, display, role)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET display = principals.display
                RETURNING user_id, display, role, created_at
                """,
                user_id,
                display,
                role,
            )
            channels = await self._channels_for(conn, user_id)
            return _row_to_principal(row, channels)
        finally:
            if own_connection:
                await conn.close()

    async def get(
        self,
        user_id: str,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> Principal | None:
        """Return the principal for ``user_id`` (with channels), else ``None``."""
        own_connection = connection is None
        conn = connection or await self._store.connect()
        try:
            await initialize_access(conn)
            row = await conn.fetchrow(
                """
                SELECT user_id, display, role, created_at
                FROM principals WHERE user_id = $1
                """,
                user_id,
            )
            if row is None:
                return None
            channels = await self._channels_for(conn, user_id)
            return _row_to_principal(row, channels)
        finally:
            if own_connection:
                await conn.close()

    async def link_channel(
        self,
        user_id: str,
        platform: str,
        channel_user_id: str,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> None:
        """Map an inbound ``(platform, channel_user_id)`` onto a principal."""
        user_id = _validate_user_id(user_id)
        own_connection = connection is None
        conn = connection or await self._store.connect()
        try:
            await initialize_access(conn)
            await conn.execute(
                """
                INSERT INTO channel_identities (platform, channel_user_id, user_id)
                VALUES ($1, $2, $3)
                ON CONFLICT (platform, channel_user_id)
                DO UPDATE SET user_id = EXCLUDED.user_id
                """,
                platform,
                channel_user_id,
                user_id,
            )
        finally:
            if own_connection:
                await conn.close()

    async def resolve_by_channel(
        self,
        platform: str,
        channel_user_id: str,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> Principal | None:
        """Return the principal mapped to a channel identity, else ``None``."""
        own_connection = connection is None
        conn = connection or await self._store.connect()
        try:
            await initialize_access(conn)
            row = await conn.fetchrow(
                """
                SELECT p.user_id, p.display, p.role, p.created_at
                FROM channel_identities ci
                JOIN principals p ON p.user_id = ci.user_id
                WHERE ci.platform = $1 AND ci.channel_user_id = $2
                """,
                platform,
                channel_user_id,
            )
            if row is None:
                return None
            channels = await self._channels_for(conn, str(row["user_id"]))
            return _row_to_principal(row, channels)
        finally:
            if own_connection:
                await conn.close()

    async def get_owner(
        self,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> Principal | None:
        """Return the current single owner principal, if one exists."""
        own_connection = connection is None
        conn = connection or await self._store.connect()
        try:
            await initialize_access(conn)
            row = await conn.fetchrow(
                """
                SELECT user_id, display, role, created_at
                FROM principals WHERE role = 'owner'
                """
            )
            if row is None:
                return None
            channels = await self._channels_for(conn, str(row["user_id"]))
            return _row_to_principal(row, channels)
        finally:
            if own_connection:
                await conn.close()

    async def transfer_owner(
        self,
        new_owner_user_id: str,
        *,
        actor: str,
        approved: bool = False,
        approval_callback: Callable[..., str] | None = None,
        demote_to: Role = "admin",
    ) -> TransferResult:
        """Atomically move the single owner role to ``new_owner_user_id``.

        Approval-gated (contract C6): the current owner must approve. The
        transfer demotes the outgoing owner (to ``demote_to``, default
        ``admin``) and promotes the target in one transaction, so the
        exactly-one-owner invariant never breaks. A C5 change-event + C6
        approval row are recorded in ``app_prod``.
        """
        new_owner_user_id = _validate_user_id(new_owner_user_id)
        if demote_to not in ROLES or demote_to == "owner":
            raise ValueError(f"Invalid demote_to role: {demote_to!r}")
        if self._store.mode != "prod":
            raise ValueError("Ownership transfer requires a prod app store")

        from hermes_cli.datastore import initialize_supabase_app

        connection = await self._store.connect()
        try:
            await initialize_supabase_app(connection)
            await initialize_access(connection)

            current = await connection.fetchrow(
                "SELECT user_id FROM principals WHERE role = 'owner'"
            )
            if current is None:
                raise ValueError(
                    "No current owner to transfer from; enroll an owner first"
                )
            from_user_id = str(current["user_id"])
            if from_user_id == new_owner_user_id:
                raise ValueError(
                    f"{new_owner_user_id!r} is already the owner"
                )
            target = await connection.fetchrow(
                "SELECT user_id FROM principals WHERE user_id = $1",
                new_owner_user_id,
            )
            if target is None:
                raise KeyError(
                    f"Target principal not enrolled: {new_owner_user_id}"
                )

            if not approved and not _request_transfer_approval(
                from_user_id,
                new_owner_user_id,
                approval_callback=approval_callback,
            ):
                raise PermissionError("Ownership transfer approval was denied")

            approval_ref = f"apr_{uuid.uuid4().hex}"
            change_ref = f"chg_{uuid.uuid4().hex}"
            op = [
                {
                    "op": "transfer_owner",
                    "path": "/principals/owner",
                    "from": from_user_id,
                    "to": new_owner_user_id,
                }
            ]
            inverse_op = [
                {
                    "op": "transfer_owner",
                    "path": "/principals/owner",
                    "from": new_owner_user_id,
                    "to": from_user_id,
                }
            ]

            async with connection.transaction():
                # Demote the outgoing owner FIRST so the partial unique index
                # never sees two owners mid-transfer.
                await connection.execute(
                    "UPDATE principals SET role = $2 WHERE user_id = $1",
                    from_user_id,
                    demote_to,
                )
                await connection.execute(
                    "UPDATE principals SET role = 'owner' WHERE user_id = $1",
                    new_owner_user_id,
                )
                await connection.execute(
                    """
                    INSERT INTO app_prod.approvals
                        (id, action, target_ref, actor, decision)
                    VALUES ($1, 'owner.transfer', $2, $3, 'approved')
                    """,
                    approval_ref,
                    f"owner:{new_owner_user_id}",
                    actor,
                )
                await connection.execute(
                    """
                    INSERT INTO app_prod.changes
                        (id, actor, mode, target_kind, op, inverse_op,
                         reversible, approval_ref, backup_ref)
                    VALUES ($1, $2, 'prod', 'data', $3::jsonb, $4::jsonb,
                            TRUE, $5, NULL)
                    """,
                    change_ref,
                    actor,
                    json.dumps(op, sort_keys=True),
                    json.dumps(inverse_op, sort_keys=True),
                    approval_ref,
                )
        finally:
            await connection.close()

        return TransferResult(
            from_user_id=from_user_id,
            to_user_id=new_owner_user_id,
            approval_ref=approval_ref,
            change_ref=change_ref,
        )


def _request_transfer_approval(
    from_user_id: str,
    to_user_id: str,
    *,
    approval_callback: Callable[..., str] | None,
) -> bool:
    from tools.approval import prompt_dangerous_approval

    choice = prompt_dangerous_approval(
        f"hermes owner transfer {to_user_id}",
        (
            f"transfer the single owner role from {from_user_id} to "
            f"{to_user_id} (irrevocable without a second transfer)"
        ),
        allow_permanent=False,
        approval_callback=approval_callback,
    )
    return choice in ("once", "session")


# ---------------------------------------------------------------------------
# resolve_principal — the gateway seam (contract C1)
# ---------------------------------------------------------------------------


def _platform_value(source: ChannelOrigin) -> str:
    platform = source.platform
    value = getattr(platform, "value", platform)
    return str(value).lower()


async def resolve_principal(
    source: ChannelOrigin,
    *,
    store: PrincipalStore,
    auto_enroll_if_paired: bool = True,
    is_paired: Callable[[str, str], bool] | None = None,
) -> Principal | None:
    """Map an inbound channel identity to a :class:`Principal` (contract C1).

    Resolution order:

    1. An existing ``channel_identities`` row wins.
    2. Otherwise, if ``auto_enroll_if_paired`` and the user is pairing-approved
       (``gateway/pairing.py`` via ``is_paired``), enrol them as ``member`` and
       link the channel identity.
    3. Otherwise return ``None`` (unenrolled / unauthorized).

    Pairing/authorization stays owned by ``gateway/pairing.py`` +
    ``gateway/authz_mixin.py``; this seam only maps an already-authorized
    identity onto a system principal.
    """
    channel_user_id = source.user_id
    if not channel_user_id:
        return None
    platform = _platform_value(source)

    connection = await store._store.connect()
    try:
        existing = await store.resolve_by_channel(
            platform, channel_user_id, connection=connection
        )
        if existing is not None:
            return existing
        if not auto_enroll_if_paired:
            return None
        if is_paired is None:
            is_paired = _default_is_paired
        if not is_paired(platform, channel_user_id):
            return None
        display = str(getattr(source, "user_name", "") or "")
        principal = await store.enroll(
            channel_user_id,
            display=display,
            role="member",
            connection=connection,
        )
        await store.link_channel(
            principal.user_id,
            platform,
            channel_user_id,
            connection=connection,
        )
        # Re-read so the returned principal carries the linked channel.
        return await store.get(principal.user_id, connection=connection)
    finally:
        await connection.close()


def _default_is_paired(platform: str, channel_user_id: str) -> bool:
    from gateway.pairing import PairingStore

    return PairingStore().is_approved(platform, channel_user_id)
