"""FG-10 — human-communications parity across Telegram + the web app.

Two humans, two surfaces, one shared brain. This module gives the human-facing
channels (Telegram + the web dashboard) the multi-user primitives the rest of
the plan already publishes, **without** re-implementing any contract:

* **Identity-aware reply routing (C1 + C4).** :func:`resolve_reply_target`
  turns a resolved :class:`~hermes_cli.access.Principal` and the originating
  :class:`~gateway.session.SessionSource` into the concrete egress target — the
  same ``account_id`` the message arrived on (FG-03 / C4), so a reply leaves via
  the correct inbox and lands with the right person. Inbound resolution is the
  existing C1 seam (``resolve_principal`` / ``gateway.inbound``); this module
  only owns the *outbound* half.

* **One pending-item surface, de-duplicated across Telegram + web.**
  :class:`NotificationStore` is a small, C2-scoped, C3-routed table of pending
  **approvals** and **proactive asks** (4.1 / 6.1). Both surfaces read the same
  rows and answer through :meth:`NotificationStore.answer`, whose update is
  atomic: whichever surface answers *first* flips the row to ``answered``; a
  second answer from the other surface is a no-op that returns the settled row
  (``newly_answered=False``). That is the "responding in one clears the other"
  guarantee, enforced in the datastore rather than by racing two UIs.

* **C6 consent / quiet-hours / rate-limit.** Delivery and auto-answer decisions
  route through :mod:`hermes_cli.consent` (contract C6, co-owned with FG-12):
  an **irreversible** approval is never auto-answered (D6); a **reversible** one
  is auto-answered only under standing consent, outside quiet-hours, and under
  the rate-limit; a proactive ask is held (not delivered) during quiet-hours.

Everything DB-touching goes through the contract-C3 datastore router
(:func:`hermes_cli.datastore.get_store`); channels are prod-only, so approvals
raised from a channel resolve to the ``app_prod`` schema. New knowledge reaches
a live conversation only as an **appended** message / tool result — this module
never mutates a system prompt or splices a toolset (prompt-cache safety).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Optional, TypeVar

from hermes_cli.access import (
    Principal,
    apply_scope_rls,
    can_read,
    normalize_visibility,
    parse_private_owner,
    private,
    scope_filter,
)
from hermes_cli.consent import ConsentPolicy, load_consent_policy

if TYPE_CHECKING:
    import asyncpg

    from gateway.session import SessionSource
    from hermes_cli.datastore import SupabaseAppStore

_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# Identity-aware reply routing (C1 + C4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplyTarget:
    """Where a reply to a resolved principal must be delivered.

    ``account_id`` is the receiving-inbox identity (contract C4): the reply
    leaves via the *same* account the inbound message arrived on so two
    accounts never cross-post and the human sees the answer where they asked.
    ``internal_user_id`` is the resolved system principal (C1); it is folded
    into the session key upstream, and carried here so egress can attribute
    the reply.
    """

    platform: str
    account_id: Optional[str]
    chat_id: str
    thread_id: Optional[str]
    internal_user_id: Optional[str]


def resolve_reply_target(
    principal: Optional[Principal],
    source: "SessionSource",
) -> ReplyTarget:
    """Resolve the egress target for a reply to ``principal`` on ``source``.

    Reuses FG-03's ``account_id`` (C4) verbatim — this does not invent a new
    routing dimension, it selects the inbox the conversation already belongs
    to. ``principal`` may be ``None`` (unenrolled sender); routing still works
    off the channel identity, only the internal-user attribution is dropped.
    """
    platform = source.platform.value
    internal = source.internal_user_id
    if principal is not None:
        # A resolved principal is authoritative for internal attribution even
        # if the source was not (re)stamped by the inbound binder.
        internal = principal.user_id
    return ReplyTarget(
        platform=platform,
        account_id=source.account_id,
        chat_id=source.chat_id,
        thread_id=source.thread_id,
        internal_user_id=internal,
    )


# ---------------------------------------------------------------------------
# Pending notifications (approvals + proactive asks), de-duplicated per surface
# ---------------------------------------------------------------------------

#: A human-facing surface a pending item can be delivered to / answered from.
#: Free-form (any gateway platform name is valid); the two first-class ones are
#: Telegram and the web app, per FG-10.
Surface = str

NotificationKind = Literal["approval", "proactive_ask"]
NotificationStatus = Literal["pending", "answered", "expired"]

NOTIFICATIONS_TABLE = "notifications"

_KINDS: tuple[NotificationKind, ...] = ("approval", "proactive_ask")
_STATUSES: tuple[NotificationStatus, ...] = ("pending", "answered", "expired")

#: Public, ordered tuple of valid notification kinds (for API validation).
NOTIFICATION_KINDS: tuple[NotificationKind, ...] = _KINDS


def parse_notification_kind(value: Optional[str]) -> Optional[NotificationKind]:
    """Validate an optional ``kind`` filter, returning the typed literal.

    ``None``/empty means "no filter"; an unknown value raises ``ValueError``.
    """
    if value is None or value == "":
        return None
    for kind in _KINDS:
        if value == kind:
            return kind
    raise ValueError(f"Unknown notification kind: {value!r}")

#: Answers that count as a grant for an approval item.
_GRANTED_ANSWERS = frozenset({"approved", "yes", "allow"})

_SELECT_COLUMNS = (
    "id, kind, owner_user_id, visibility, title, body, command, reversible, "
    "status, answer, answered_by, answered_via, dedupe_key, delivered, "
    "created_at, answered_at"
)

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {NOTIFICATIONS_TABLE} (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('approval', 'proactive_ask')),
    owner_user_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    command TEXT NOT NULL DEFAULT '',
    reversible BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'answered', 'expired')),
    answer TEXT,
    answered_by TEXT,
    answered_via TEXT,
    dedupe_key TEXT,
    delivered BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered_at TIMESTAMPTZ
);
-- Cross-surface idempotency: at most one *pending* item per (owner, dedupe_key)
-- so the same approval raised for Telegram and web collapses onto one row.
CREATE UNIQUE INDEX IF NOT EXISTS {NOTIFICATIONS_TABLE}_dedupe
    ON {NOTIFICATIONS_TABLE} (owner_user_id, dedupe_key)
    WHERE status = 'pending' AND dedupe_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS {NOTIFICATIONS_TABLE}_pending_idx
    ON {NOTIFICATIONS_TABLE} (status, owner_user_id);
"""


@dataclass(frozen=True)
class Notification:
    """One pending (or settled) human-facing item, C2-scoped by ``visibility``."""

    id: str
    kind: NotificationKind
    owner_user_id: str
    visibility: str
    title: str
    body: str
    command: str
    reversible: bool
    status: NotificationStatus
    answer: Optional[str]
    answered_by: Optional[str]
    answered_via: Optional[str]
    dedupe_key: Optional[str]
    delivered: bool
    created_at: Optional[datetime]
    answered_at: Optional[datetime]

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    @property
    def granted(self) -> bool:
        """Whether a settled approval was answered as a grant."""
        return bool(self.answer) and self.answer in _GRANTED_ANSWERS

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "title": self.title,
            "body": self.body,
            "command": self.command,
            "reversible": self.reversible,
            "status": self.status,
            "answer": self.answer,
            "answered_by": self.answered_by,
            "answered_via": self.answered_via,
            "delivered": self.delivered,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
        }

    @classmethod
    def _from_row(cls, row: "asyncpg.Record") -> "Notification":
        return cls(
            id=row["id"],
            kind=row["kind"],
            owner_user_id=row["owner_user_id"],
            visibility=row["visibility"],
            title=row["title"],
            body=row["body"],
            command=row["command"],
            reversible=row["reversible"],
            status=row["status"],
            answer=row["answer"],
            answered_by=row["answered_by"],
            answered_via=row["answered_via"],
            dedupe_key=row["dedupe_key"],
            delivered=row["delivered"],
            created_at=row["created_at"],
            answered_at=row["answered_at"],
        )


@dataclass(frozen=True)
class CreateResult:
    """Outcome of raising a pending item."""

    notification: Notification
    #: True when this call inserted a new row (False = an existing pending item
    #: with the same dedupe key was returned instead — cross-surface collapse).
    created: bool
    #: True when C6 auto-answered a reversible approval under standing consent.
    auto_answered: bool
    #: Whether the item may be pushed to a surface now (False during quiet-hours
    #: for a proactive ask — the row is held, to be delivered when quiet-hours end).
    deliver_now: bool


@dataclass(frozen=True)
class AnswerResult:
    """Outcome of answering a pending item (the dedupe decision)."""

    notification: Notification
    #: True when THIS call settled a previously-pending item; False when the
    #: item was already answered (the other surface won the race — no-op).
    newly_answered: bool


class NotificationError(RuntimeError):
    """Base class for notification-surface failures."""


class NotificationNotFound(NotificationError):
    """No such notification id."""


class NotificationStore:
    """C2-scoped, C3-routed store of pending approvals + proactive asks (C6).

    Reads are filtered by :func:`hermes_cli.access.scope_filter` and Postgres
    row-level security (:func:`apply_scope_rls`) is the DB-level backstop, so a
    member never sees another member's ``private:<user>`` item while the owner
    sees all. Delivery / auto-answer decisions defer to the C6 consent policy.
    """

    def __init__(
        self,
        store: "SupabaseAppStore",
        *,
        config: Optional[dict[str, object]] = None,
        policy: Optional[ConsentPolicy] = None,
    ) -> None:
        from hermes_cli.datastore import SupabaseAppStore

        if not isinstance(store, SupabaseAppStore):
            raise TypeError("NotificationStore requires a supabase-app store")
        self._store = store
        self._policy = policy or load_consent_policy(config)

    @property
    def mode(self) -> str:
        return self._store.mode

    @property
    def policy(self) -> ConsentPolicy:
        return self._policy

    async def _connect(self) -> "asyncpg.Connection":
        conn = await self._store.connect()
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._store.schema}"')
        return conn

    async def initialize(
        self, *, connection: "Optional[asyncpg.Connection]" = None
    ) -> None:
        """Create the notifications table + its RLS policy (idempotent)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await conn.execute(_SCHEMA_SQL)
            await apply_scope_rls(conn, NOTIFICATIONS_TABLE)
        finally:
            if own:
                await conn.close()

    # -- creating ----------------------------------------------------------

    async def create(
        self,
        *,
        kind: NotificationKind,
        target_user_id: str,
        title: str,
        body: str = "",
        command: str = "",
        reversible: bool = True,
        visibility: Optional[str] = None,
        dedupe_key: Optional[str] = None,
        now: Optional[datetime] = None,
        recent_auto_approvals: int = 0,
        connection: "Optional[asyncpg.Connection]" = None,
    ) -> CreateResult:
        """Raise a pending item for ``target_user_id``, applying C6.

        * ``dedupe_key`` collapses a re-raise onto the existing pending row (the
          same approval offered to Telegram *and* web is one item, not two).
        * A **reversible approval** is auto-answered ``approved`` when the C6
          policy grants standing consent (outside quiet-hours, under the rate
          limit). An **irreversible** approval is never auto-answered (D6).
        * A **proactive ask** is held (``deliver_now=False``) during quiet-hours.

        The item defaults to ``private:<target_user_id>`` visibility (only the
        target and the owner may see/answer it); pass ``visibility`` to widen.
        """
        if kind not in _KINDS:
            raise ValueError(f"Unknown notification kind: {kind!r}")
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("A notification requires a non-empty title")
        resolved_visibility = normalize_visibility(
            visibility if visibility is not None else private(target_user_id)
        )
        now = now or datetime.now()

        auto_answered = False
        answer: Optional[str] = None
        answered_via: Optional[str] = None
        status: NotificationStatus = "pending"
        if kind == "approval" and self._may_auto_approve(
            reversible=reversible, now=now, recent_auto_approvals=recent_auto_approvals
        ):
            auto_answered = True
            answer = "approved"
            answered_via = "auto"
            status = "answered"

        deliver_now = True
        if kind == "proactive_ask" and self._policy.within_quiet_hours(now):
            deliver_now = False

        new_id = f"ntf_{uuid.uuid4().hex}"

        async def _write(conn: "asyncpg.Connection") -> CreateResult:
            if dedupe_key is not None:
                existing = await conn.fetchrow(
                    f"""
                    SELECT {_SELECT_COLUMNS} FROM {NOTIFICATIONS_TABLE}
                    WHERE owner_user_id = $1 AND dedupe_key = $2
                      AND status = 'pending'
                    """,
                    target_user_id,
                    dedupe_key,
                )
                if existing is not None:
                    return CreateResult(
                        Notification._from_row(existing),
                        created=False,
                        auto_answered=False,
                        deliver_now=deliver_now,
                    )
            row = await conn.fetchrow(
                f"""
                INSERT INTO {NOTIFICATIONS_TABLE}
                    (id, kind, owner_user_id, visibility, title, body, command,
                     reversible, status, answer, answered_via, dedupe_key,
                     delivered, answered_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                        {'NOW()' if status == 'answered' else 'NULL'})
                RETURNING {_SELECT_COLUMNS}
                """,
                new_id,
                kind,
                target_user_id,
                resolved_visibility,
                clean_title,
                body or "",
                command or "",
                reversible,
                status,
                answer,
                answered_via,
                dedupe_key,
                deliver_now,
            )
            return CreateResult(
                Notification._from_row(row),
                created=True,
                auto_answered=auto_answered,
                deliver_now=deliver_now,
            )

        return await self._with_connection(_write, connection)

    def _may_auto_approve(
        self, *, reversible: bool, now: datetime, recent_auto_approvals: int
    ) -> bool:
        """C6 auto-answer test for a reversible approval (never irreversible)."""
        if not reversible:
            return False
        if not self._policy.auto_approve_reversible:
            return False
        if self._policy.within_quiet_hours(now):
            return False
        if self._policy.is_rate_limited(recent_auto_approvals):
            return False
        return True

    # -- reading -----------------------------------------------------------

    async def list_pending(
        self,
        principal: Principal,
        *,
        kind: Optional[NotificationKind] = None,
        connection: "Optional[asyncpg.Connection]" = None,
    ) -> list[Notification]:
        """List pending items ``principal`` may see (C2-scoped), newest first."""
        predicate = scope_filter(principal, column="visibility", start_index=1)
        where = [predicate.sql, "status = 'pending'"]
        params: list[object] = list(predicate.params)
        if kind is not None:
            if kind not in _KINDS:
                raise ValueError(f"Unknown notification kind: {kind!r}")
            where.append(f"kind = ${len(params) + 1}")
            params.append(kind)
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM {NOTIFICATIONS_TABLE} "
            f"WHERE {' AND '.join(where)} ORDER BY created_at DESC"
        )

        async def _read(conn: "asyncpg.Connection") -> list[Notification]:
            rows = await conn.fetch(sql, *params)
            return [Notification._from_row(r) for r in rows]

        return await self._with_connection(_read, connection)

    async def get(
        self,
        notification_id: str,
        principal: Principal,
        *,
        connection: "Optional[asyncpg.Connection]" = None,
    ) -> Notification:
        """Fetch one item, enforcing C2 visibility for ``principal``."""

        async def _read(conn: "asyncpg.Connection") -> Notification:
            return await self._load_visible(conn, notification_id, principal)

        return await self._with_connection(_read, connection)

    # -- answering (the cross-surface dedupe point) ------------------------

    async def answer(
        self,
        notification_id: str,
        principal: Principal,
        *,
        answer: str,
        via: Surface,
        connection: "Optional[asyncpg.Connection]" = None,
    ) -> AnswerResult:
        """Answer a pending item; idempotent across surfaces (the dedupe point).

        Enforces C2 first (a member may not answer another member's private
        item; the owner may answer any). The settling UPDATE is conditional on
        ``status = 'pending'``: whichever surface lands first wins and gets
        ``newly_answered=True``; a later answer from the *other* surface finds
        the row already settled and returns it unchanged
        (``newly_answered=False``). That is "responding in one clears the
        other", enforced atomically in the datastore.
        """
        clean_answer = (answer or "").strip()
        if not clean_answer:
            raise ValueError("answer must be a non-empty string")
        clean_via = (via or "").strip()
        if not clean_via:
            raise ValueError("via (the answering surface) is required")

        async def _run(conn: "asyncpg.Connection") -> AnswerResult:
            # C2 gate: the caller must be able to read the item to answer it.
            current = await self._load_visible(conn, notification_id, principal)
            if not current.is_pending:
                return AnswerResult(current, newly_answered=False)
            row = await conn.fetchrow(
                f"""
                UPDATE {NOTIFICATIONS_TABLE}
                SET status = 'answered', answer = $2, answered_by = $3,
                    answered_via = $4, answered_at = NOW()
                WHERE id = $1 AND status = 'pending'
                RETURNING {_SELECT_COLUMNS}
                """,
                notification_id,
                clean_answer,
                principal.user_id,
                clean_via,
            )
            if row is None:
                # Lost the race between the read and the update — reload the
                # now-settled row and report it as a no-op (dedupe).
                settled = await self._load_visible(conn, notification_id, principal)
                return AnswerResult(settled, newly_answered=False)
            return AnswerResult(Notification._from_row(row), newly_answered=True)

        return await self._with_connection(_run, connection)

    # -- internals ---------------------------------------------------------

    async def _load_visible(
        self,
        conn: "asyncpg.Connection",
        notification_id: str,
        principal: Principal,
    ) -> Notification:
        row = await conn.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM {NOTIFICATIONS_TABLE} WHERE id = $1",
            notification_id,
        )
        if row is None:
            raise NotificationNotFound(f"No such notification: {notification_id}")
        item = Notification._from_row(row)
        if not can_read(principal, item.visibility):
            raise PermissionError(
                f"{principal.user_id} may not access notification {notification_id}"
            )
        return item

    async def _with_connection(
        self,
        fn: Callable[["asyncpg.Connection"], Awaitable[_T]],
        connection: "Optional[asyncpg.Connection]",
    ) -> _T:
        if connection is not None:
            return await fn(connection)
        conn = await self._connect()
        try:
            return await fn(conn)
        finally:
            await conn.close()


def notification_target_user(item: Notification) -> str:
    """Best-effort resolve the intended human for ``item``.

    Prefers the ``private:<user>`` owner embedded in the visibility tag (the
    person the item is addressed to); falls back to ``owner_user_id``.
    """
    embedded = parse_private_owner(item.visibility)
    return embedded or item.owner_user_id
