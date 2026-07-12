"""C8 interaction tracing for the Supabase application datastore.

The active trace is context-local and observational: emitters append compact
metadata to an in-memory buffer, and the gateway flushes that buffer beside the
conversation path. Trace data is never read back into a live model turn.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterator, Literal, Mapping, Sequence, cast

from hermes_cli.access import Principal
from hermes_cli.datastore import SessionOrigin, SupabaseAppStore, get_store

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.datastore import StoreMode

logger = logging.getLogger(__name__)

InteractionKind = Literal[
    "inbound",
    "turn",
    "tool_call",
    "tool_result",
    "outbound",
    "approval",
    "change",
    "cost",
    "error",
    "core_denied",
]

INTERACTION_KINDS: tuple[InteractionKind, ...] = (
    "inbound",
    "turn",
    "tool_call",
    "tool_result",
    "outbound",
    "approval",
    "change",
    "cost",
    "error",
    "core_denied",
)
_INTERACTION_KINDS = frozenset(INTERACTION_KINDS)
_MAINTENANCE_INTERVAL_SECONDS = 3600.0
_last_maintenance_at: dict[str, float] = {}
_maintenance_lock = threading.Lock()
_initialized_stores: set[str] = set()
_initialization_locks: dict[tuple[int, str], asyncio.Lock] = {}
_initialization_lock = threading.Lock()


@dataclass(frozen=True)
class ActionTrackingConfig:
    enabled: bool = True
    retention_days: int = 30
    rollup: bool = True
    sample: float = 1.0

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, object] | None,
    ) -> "ActionTrackingConfig":
        raw_section = (config or {}).get("action_tracking", {})
        section = (
            cast(Mapping[str, object], raw_section)
            if isinstance(raw_section, Mapping)
            else {}
        )
        enabled_value = section.get("enabled", True)
        enabled = enabled_value if isinstance(enabled_value, bool) else True
        try:
            retention_days = int(str(section.get("retention_days", 30)))
        except (TypeError, ValueError):
            retention_days = 30
        if retention_days < 1:
            retention_days = 1
        rollup_value = section.get("rollup", True)
        rollup = rollup_value if isinstance(rollup_value, bool) else True
        try:
            sample = float(str(section.get("sample", 1.0)))
        except (TypeError, ValueError):
            sample = 1.0
        sample = max(0.0, min(1.0, sample))
        return cls(
            enabled=enabled,
            retention_days=retention_days,
            rollup=rollup,
            sample=sample,
        )


@dataclass(frozen=True)
class Interaction:
    id: str
    trace_id: str
    parent_id: str | None
    ts: datetime
    actor_user_id: str
    session_key: str
    platform: str
    kind: InteractionKind
    ref: str
    summary: str
    payload_ref: str | None
    mode: str

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "Interaction":
        return cls(
            id=str(row["id"]),
            trace_id=str(row["trace_id"]),
            parent_id=(
                str(row["parent_id"]) if row.get("parent_id") is not None else None
            ),
            ts=cast(datetime, row["ts"]),
            actor_user_id=str(row["actor_user_id"]),
            session_key=str(row["session_key"]),
            platform=str(row["platform"]),
            kind=cast(InteractionKind, row["kind"]),
            ref=str(row["ref"]),
            summary=str(row["summary"]),
            payload_ref=(
                str(row["payload_ref"]) if row.get("payload_ref") is not None else None
            ),
            mode=str(row["mode"]),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "ts": self.ts.isoformat(),
            "actor_user_id": self.actor_user_id,
            "session_key": self.session_key,
            "platform": self.platform,
            "kind": self.kind,
            "ref": self.ref,
            "summary": self.summary,
            "payload_ref": self.payload_ref,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class TraceSummary:
    trace_id: str
    first_ts: datetime
    last_ts: datetime
    actor_user_id: str
    session_key: str
    platform: str
    mode: str
    event_count: int
    kind_counts: dict[str, int]
    rolled_up: bool = False

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "TraceSummary":
        raw_counts = row["kind_counts"]
        if isinstance(raw_counts, str):
            raw_counts = json.loads(raw_counts)
        counts = {
            str(key): int(str(value))
            for key, value in cast(Mapping[object, object], raw_counts).items()
        }
        return cls(
            trace_id=str(row["trace_id"]),
            first_ts=cast(datetime, row["first_ts"]),
            last_ts=cast(datetime, row["last_ts"]),
            actor_user_id=str(row["actor_user_id"]),
            session_key=str(row["session_key"]),
            platform=str(row["platform"]),
            mode=str(row["mode"]),
            event_count=int(str(row["event_count"])),
            kind_counts=counts,
            rolled_up=bool(row["rolled_up"]),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "first_ts": self.first_ts.isoformat(),
            "last_ts": self.last_ts.isoformat(),
            "actor_user_id": self.actor_user_id,
            "session_key": self.session_key,
            "platform": self.platform,
            "mode": self.mode,
            "event_count": self.event_count,
            "kind_counts": self.kind_counts,
            "rolled_up": self.rolled_up,
        }


class InteractionTrace:
    """One immutable trace id plus a thread-safe append-only event buffer."""

    def __init__(
        self,
        *,
        actor_user_id: str,
        session_key: str,
        platform: str,
        mode: "StoreMode",
        sample: float = 1.0,
        trace_id: str | None = None,
    ) -> None:
        self.trace_id = trace_id or f"trc_{uuid.uuid4().hex}"
        self.actor_user_id = actor_user_id
        self.session_key = session_key
        self.platform = platform
        self.mode = mode
        self.sample = max(0.0, min(1.0, float(sample)))
        self._events: list[Interaction] = []
        self._event_by_ref: dict[tuple[str, str], str] = {}
        self._sampled_tools: dict[str, bool] = {}
        self._lock = threading.RLock()

    @property
    def events(self) -> tuple[Interaction, ...]:
        with self._lock:
            return tuple(self._events)

    def _tool_is_sampled(self, tool_call_id: str) -> bool:
        if self.sample >= 1.0:
            return True
        if self.sample <= 0.0:
            return False
        with self._lock:
            sampled = self._sampled_tools.get(tool_call_id)
            if sampled is not None:
                return sampled
            digest = hashlib.sha256(
                f"{self.trace_id}:{tool_call_id}".encode("utf-8")
            ).digest()
            sampled = int.from_bytes(digest[:8], "big") / float(2**64) < self.sample
            self._sampled_tools[tool_call_id] = sampled
            return sampled

    def parent_for(self, *, turn_id: str = "", tool_call_id: str = "") -> str | None:
        with self._lock:
            if tool_call_id:
                parent = self._event_by_ref.get(("tool_call", tool_call_id))
                if parent:
                    return parent
            if turn_id:
                parent = self._event_by_ref.get(("turn", turn_id))
                if parent:
                    return parent
            for key in ("turn", "inbound"):
                for event in reversed(self._events):
                    if event.kind == key:
                        return event.id
        return None

    def emit(
        self,
        kind: InteractionKind,
        *,
        ref: str,
        summary: str,
        parent_id: str | None = None,
        payload_ref: str | None = None,
        ts: datetime | None = None,
    ) -> str | None:
        if kind not in _INTERACTION_KINDS:
            raise ValueError(f"Unknown interaction kind: {kind!r}")
        if kind in {"tool_call", "tool_result"} and not self._tool_is_sampled(ref):
            return None
        interaction = Interaction(
            id=f"int_{uuid.uuid4().hex}",
            trace_id=self.trace_id,
            parent_id=parent_id,
            ts=ts or datetime.now(timezone.utc),
            actor_user_id=self.actor_user_id,
            session_key=self.session_key,
            platform=self.platform,
            kind=kind,
            ref=ref,
            summary=summary[:500],
            payload_ref=payload_ref,
            mode=self.mode,
        )
        with self._lock:
            self._events.append(interaction)
            self._event_by_ref.setdefault((kind, ref), interaction.id)
        return interaction.id

    def ensure_tool_call(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        turn_id: str = "",
        started_at: datetime | None = None,
    ) -> str | None:
        if not self._tool_is_sampled(tool_call_id):
            return None
        with self._lock:
            existing = self._event_by_ref.get(("tool_call", tool_call_id))
        if existing:
            return existing
        return self.emit(
            "tool_call",
            ref=tool_call_id,
            summary=tool_name,
            parent_id=self.parent_for(turn_id=turn_id),
            ts=started_at,
        )

    def emit_tool_result(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        status: str,
        turn_id: str = "",
        duration_ms: int = 0,
    ) -> str | None:
        if not self._tool_is_sampled(tool_call_id):
            return None
        with self._lock:
            existing = self._event_by_ref.get(("tool_result", tool_call_id))
        if existing:
            return existing
        now = datetime.now(timezone.utc)
        started_at = now - timedelta(milliseconds=max(0, duration_ms))
        parent_id = self.ensure_tool_call(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            turn_id=turn_id,
            started_at=started_at,
        )
        return self.emit(
            "tool_result",
            ref=tool_call_id,
            summary=f"{tool_name}: {status}",
            parent_id=parent_id or self.parent_for(turn_id=turn_id),
            ts=now,
        )


_active_trace: contextvars.ContextVar[InteractionTrace | None] = contextvars.ContextVar(
    "interaction_trace",
    default=None,
)


def current_trace() -> InteractionTrace | None:
    return _active_trace.get()


def current_trace_id() -> str | None:
    trace = current_trace()
    return trace.trace_id if trace is not None else None


@contextmanager
def bind_trace(trace: InteractionTrace | None) -> Iterator[InteractionTrace | None]:
    token = _active_trace.set(trace)
    try:
        yield trace
    finally:
        _active_trace.reset(token)


def _current_causation_ids() -> tuple[str, str]:
    try:
        from tools.approval import get_current_observability_context

        return get_current_observability_context()
    except Exception:
        return "", ""


def observe(
    kind: InteractionKind,
    *,
    ref: str,
    summary: str,
    parent_id: str | None = None,
    payload_ref: str | None = None,
) -> str | None:
    trace = current_trace()
    if trace is None:
        return None
    if parent_id is None and kind != "inbound":
        turn_id, tool_call_id = _current_causation_ids()
        parent_id = trace.parent_for(turn_id=turn_id, tool_call_id=tool_call_id)
    return trace.emit(
        kind,
        ref=ref,
        summary=summary,
        parent_id=parent_id,
        payload_ref=payload_ref,
    )


def observe_tool_call(
    *,
    tool_call_id: str,
    tool_name: str,
    turn_id: str = "",
) -> str | None:
    trace = current_trace()
    if trace is None:
        return None
    return trace.ensure_tool_call(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        turn_id=turn_id,
    )


def observe_tool_result(
    *,
    tool_call_id: str,
    tool_name: str,
    status: str,
    turn_id: str = "",
    duration_ms: int = 0,
) -> str | None:
    trace = current_trace()
    if trace is None:
        return None
    return trace.emit_tool_result(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        status=status,
        turn_id=turn_id,
        duration_ms=duration_ms,
    )


def create_gateway_trace(
    *,
    config: Mapping[str, object],
    source: SessionOrigin,
    actor_user_id: str,
    session_key: str,
    platform: str,
) -> tuple[InteractionTrace | None, "InteractionLedger | None"]:
    settings = ActionTrackingConfig.from_config(config)
    if not settings.enabled:
        return None, None
    store = get_store("supabase-app", source=source, config=config)
    if not isinstance(store, SupabaseAppStore):
        raise TypeError("Interaction tracing requires a supabase-app store")
    trace = InteractionTrace(
        actor_user_id=actor_user_id,
        session_key=session_key,
        platform=platform,
        mode=store.mode,
        sample=settings.sample,
    )
    ledger = InteractionLedger(store, config=config) if store.dsn else None
    return trace, ledger


def _actor_predicate(principal: Principal, *, start_index: int = 1) -> tuple[str, tuple]:
    if principal.is_owner:
        return "TRUE", ()
    return f"actor_user_id = ${start_index}", (principal.user_id,)


async def initialize_interactions(connection: "asyncpg.Connection") -> None:
    kinds = ", ".join(f"'{kind}'" for kind in INTERACTION_KINDS)
    for schema, mode in (("app_dev", "dev"), ("app_prod", "prod")):
        table = f"{schema}.interactions"
        rollups = f"{schema}.interaction_rollups"
        await connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                parent_id TEXT,
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                actor_user_id TEXT NOT NULL,
                session_key TEXT NOT NULL,
                platform TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ({kinds})),
                ref TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                payload_ref TEXT,
                mode TEXT NOT NULL DEFAULT '{mode}' CHECK (mode = '{mode}')
            );
            CREATE INDEX IF NOT EXISTS interactions_trace_ts_idx
                ON {table} (trace_id, ts);
            CREATE INDEX IF NOT EXISTS interactions_actor_ts_idx
                ON {table} (actor_user_id, ts DESC);
            CREATE INDEX IF NOT EXISTS interactions_retention_idx
                ON {table} (ts);

            CREATE TABLE IF NOT EXISTS {rollups} (
                trace_id TEXT PRIMARY KEY,
                first_ts TIMESTAMPTZ NOT NULL,
                last_ts TIMESTAMPTZ NOT NULL,
                actor_user_id TEXT NOT NULL,
                session_key TEXT NOT NULL,
                platform TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT '{mode}' CHECK (mode = '{mode}'),
                event_count BIGINT NOT NULL,
                kind_counts JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS interaction_rollups_actor_ts_idx
                ON {rollups} (actor_user_id, last_ts DESC);
            """
        )
        for scoped_table in (table, rollups):
            policy = f"{scoped_table.replace('.', '_')}_actor_read"
            await connection.execute(
                f"""
                ALTER TABLE {scoped_table} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE {scoped_table} FORCE ROW LEVEL SECURITY;
                DROP POLICY IF EXISTS {policy} ON {scoped_table};
                CREATE POLICY {policy} ON {scoped_table}
                    FOR SELECT
                    USING (
                        current_setting('hermes.principal_role', true) = 'owner'
                        OR actor_user_id =
                           current_setting('hermes.principal_id', true)
                    );
                """
            )


class InteractionLedger:
    """Append/query/retention sink for one C3-routed application schema."""

    def __init__(
        self,
        store: SupabaseAppStore | None = None,
        *,
        config: Mapping[str, object] | None = None,
    ) -> None:
        if store is None:
            resolved = get_store("supabase-app", config=config)
            if not isinstance(resolved, SupabaseAppStore):
                raise TypeError("InteractionLedger requires a supabase-app store")
            store = resolved
        self._store = store
        self._settings = ActionTrackingConfig.from_config(config)
        self._table = f"{store.schema}.interactions"
        self._rollups = f"{store.schema}.interaction_rollups"

    @property
    def _store_key(self) -> str:
        return f"{self._store.dsn}:{self._store.schema}"

    async def _ensure_initialized(
        self,
        connection: "asyncpg.Connection | None" = None,
    ) -> None:
        key = self._store_key
        with _initialization_lock:
            if key in _initialized_stores:
                return
            lock_key = (id(asyncio.get_running_loop()), key)
            lock = _initialization_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            with _initialization_lock:
                if key in _initialized_stores:
                    return
            own = connection is None
            conn = connection or await self._store.connect()
            try:
                from hermes_cli.datastore import initialize_supabase_app

                await initialize_supabase_app(conn)
            finally:
                if own:
                    await conn.close()
            with _initialization_lock:
                _initialized_stores.add(key)

    async def initialize(
        self,
        *,
        connection: "asyncpg.Connection | None" = None,
    ) -> None:
        own = connection is None
        conn = connection or await self._store.connect()
        try:
            from hermes_cli.datastore import initialize_supabase_app

            await initialize_supabase_app(conn)
            with _initialization_lock:
                _initialized_stores.add(self._store_key)
        finally:
            if own:
                await conn.close()

    async def append(
        self,
        interaction: Interaction,
        *,
        connection: "asyncpg.Connection | None" = None,
    ) -> None:
        await self.append_many([interaction], connection=connection)

    async def append_many(
        self,
        interactions: Sequence[Interaction],
        *,
        connection: "asyncpg.Connection | None" = None,
    ) -> None:
        if not interactions:
            return
        own = connection is None
        conn = connection or await self._store.connect()
        try:
            await self._ensure_initialized(conn)
            await conn.executemany(
                f"""
                INSERT INTO {self._table}
                    (id, trace_id, parent_id, ts, actor_user_id, session_key,
                     platform, kind, ref, summary, payload_ref, mode)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (id) DO NOTHING
                """,
                [
                    (
                        item.id,
                        item.trace_id,
                        item.parent_id,
                        item.ts,
                        item.actor_user_id,
                        item.session_key,
                        item.platform,
                        item.kind,
                        item.ref,
                        item.summary,
                        item.payload_ref,
                        item.mode,
                    )
                    for item in interactions
                ],
            )
        finally:
            if own:
                await conn.close()

    async def flush(
        self,
        trace: InteractionTrace,
        *,
        connection: "asyncpg.Connection | None" = None,
    ) -> None:
        await self.append_many(trace.events, connection=connection)
        if connection is None:
            await self._maybe_maintain()

    async def list_traces(
        self,
        principal: Principal,
        *,
        limit: int = 100,
        connection: "asyncpg.Connection | None" = None,
    ) -> list[TraceSummary]:
        predicate, params = _actor_predicate(principal, start_index=1)
        limit_placeholder = f"${len(params) + 1}"
        own = connection is None
        conn = connection or await self._store.connect()
        try:
            await self._ensure_initialized(conn)
            rows = await conn.fetch(
                f"""
                WITH live AS (
                    SELECT trace_id, MIN(ts) AS first_ts, MAX(ts) AS last_ts,
                           MIN(actor_user_id) AS actor_user_id,
                           MIN(session_key) AS session_key,
                           MIN(platform) AS platform,
                           MIN(mode) AS mode,
                           COUNT(*) AS event_count,
                           jsonb_object_agg(kind, kind_count) AS kind_counts,
                           FALSE AS rolled_up
                    FROM (
                        SELECT trace_id, ts, actor_user_id, session_key,
                               platform, mode, kind, COUNT(*) OVER (
                                   PARTITION BY trace_id, kind
                               ) AS kind_count
                        FROM {self._table}
                        WHERE {predicate}
                    ) scoped
                    GROUP BY trace_id
                ),
                archived AS (
                    SELECT trace_id, first_ts, last_ts, actor_user_id,
                           session_key, platform, mode, event_count,
                           kind_counts, TRUE AS rolled_up
                    FROM {self._rollups}
                    WHERE {predicate}
                )
                SELECT * FROM live
                UNION ALL
                SELECT * FROM archived
                ORDER BY last_ts DESC
                LIMIT {limit_placeholder}
                """,
                *params,
                limit,
            )
            return [TraceSummary.from_row(row) for row in rows]
        finally:
            if own:
                await conn.close()

    async def get_trace(
        self,
        trace_id: str,
        principal: Principal,
        *,
        connection: "asyncpg.Connection | None" = None,
    ) -> tuple[list[Interaction], TraceSummary | None]:
        predicate, params = _actor_predicate(principal, start_index=2)
        own = connection is None
        conn = connection or await self._store.connect()
        try:
            await self._ensure_initialized(conn)
            rows = await conn.fetch(
                f"""
                SELECT id, trace_id, parent_id, ts, actor_user_id, session_key,
                       platform, kind, ref, summary, payload_ref, mode
                FROM {self._table}
                WHERE trace_id = $1 AND {predicate}
                ORDER BY ts, id
                """,
                trace_id,
                *params,
            )
            if rows:
                return [Interaction.from_row(row) for row in rows], None
            rollup = await conn.fetchrow(
                f"""
                SELECT trace_id, first_ts, last_ts, actor_user_id, session_key,
                       platform, mode, event_count, kind_counts,
                       TRUE AS rolled_up
                FROM {self._rollups}
                WHERE trace_id = $1 AND {predicate}
                """,
                trace_id,
                *params,
            )
            return [], TraceSummary.from_row(rollup) if rollup else None
        finally:
            if own:
                await conn.close()

    async def apply_retention(
        self,
        *,
        now: datetime | None = None,
        connection: "asyncpg.Connection | None" = None,
    ) -> dict[str, int]:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(
            days=self._settings.retention_days
        )
        own = connection is None
        conn = connection or await self._store.connect()
        try:
            await self._ensure_initialized(conn)
            async with conn.transaction():
                rolled_up = 0
                if self._settings.rollup:
                    rollup_result = await conn.execute(
                        f"""
                        INSERT INTO {self._rollups}
                            (trace_id, first_ts, last_ts, actor_user_id,
                             session_key, platform, mode, event_count,
                             kind_counts)
                        SELECT trace_id, MIN(ts), MAX(ts), MIN(actor_user_id),
                               MIN(session_key), MIN(platform), MIN(mode),
                               COUNT(*),
                               jsonb_object_agg(kind, kind_count)
                        FROM (
                            SELECT item.trace_id, item.ts, item.actor_user_id,
                                   item.session_key,
                                   platform, mode, kind, COUNT(*) OVER (
                                       PARTITION BY item.trace_id, kind
                                   ) AS kind_count
                            FROM {self._table} item
                            INNER JOIN (
                                SELECT trace_id
                                FROM {self._table}
                                GROUP BY trace_id
                                HAVING MAX(ts) < $1
                            ) expired USING (trace_id)
                        ) old
                        GROUP BY trace_id
                        ON CONFLICT (trace_id) DO NOTHING
                        """,
                        cutoff,
                    )
                    rolled_up = int(rollup_result.rsplit(" ", 1)[-1])
                result = await conn.execute(
                    f"""
                    DELETE FROM {self._table}
                    WHERE trace_id IN (
                        SELECT trace_id
                        FROM {self._table}
                        GROUP BY trace_id
                        HAVING MAX(ts) < $1
                    )
                    """,
                    cutoff,
                )
                return {
                    "rolled_up": rolled_up,
                    "deleted": int(result.rsplit(" ", 1)[-1]),
                }
        finally:
            if own:
                await conn.close()

    async def _maybe_maintain(self) -> None:
        key = f"{self._store.dsn}:{self._store.schema}"
        now = time.monotonic()
        with _maintenance_lock:
            previous = _last_maintenance_at.get(key, 0.0)
            if now - previous < _MAINTENANCE_INTERVAL_SECONDS:
                return
            _last_maintenance_at[key] = now
        try:
            await self.apply_retention()
        except Exception as exc:
            logger.warning("Interaction retention maintenance failed: %s", exc)
