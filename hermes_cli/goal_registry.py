"""Prioritised, measurable multi-goal registry (FG-04).

This registry sits **above** the per-session Ralph loop in
:mod:`hermes_cli.goals` — it does not replace it. The Ralph loop
(``GoalManager`` / ``judge_goal``) remains the *execution* mechanism for one
active goal in one session; this registry is the durable, cross-session,
cross-user record of *which* goals exist, how they are prioritised, and how
their **measurable** success criteria are progressing.

Contracts consumed (never re-implemented):

* **C3** — every connection is obtained through
  :class:`hermes_cli.datastore.SupabaseAppStore`, so the ``app_dev`` /
  ``app_prod`` schema follows the resolved mode. Channels are forced to prod by
  the router; this module never opens a raw connection or routes modes itself.
* **C2** — every ``goals`` row carries ``owner_user_id`` + ``visibility``
  (``shared`` | ``private:<user_id>``). Reads are filtered by
  :func:`hermes_cli.access.scope_filter` and Postgres row-level security
  (:func:`hermes_cli.access.apply_scope_rls`) is the database-level backstop on
  the ``goals`` table. Child rows (metrics/progress/asks) are reached only
  through a scoped join to their parent goal, so a private goal's metrics are
  never returned to a member who cannot read the goal.

The metric maths (achieved? / incremental progress?) lives on
:class:`hermes_cli.goals.GoalMetric` so "done" is *computed*, not vibes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from hermes_cli.access import (
    Principal,
    SHARED,
    apply_scope_rls,
    normalize_visibility,
    scope_filter,
)
from hermes_cli.goals import (
    DEFAULT_GOAL_PRIORITY,
    GoalMetric,
    normalize_priority,
    priority_rank,
    priority_weight,
)

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.datastore import SupabaseAppStore


#: The one C2-scoped table (RLS is applied to it). Metrics/progress/asks hang
#: off it and are reached only via a scoped join.
GOALS_TABLE = "goals"

#: Goal lifecycle vocabulary. An ordered tuple so tests assert membership /
#: transitions, not a frozen count.
GOAL_STATUSES: Tuple[str, ...] = ("active", "paused", "done", "cleared")
_ACTIVE_STATUS = "active"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {GOALS_TABLE} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deadline TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS {GOALS_TABLE}_visibility_idx
    ON {GOALS_TABLE} (visibility);
CREATE INDEX IF NOT EXISTS {GOALS_TABLE}_status_idx
    ON {GOALS_TABLE} (status);

CREATE TABLE IF NOT EXISTS goal_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID NOT NULL REFERENCES {GOALS_TABLE}(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    target DOUBLE PRECISION,
    current DOUBLE PRECISION NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT '',
    source_query TEXT NOT NULL DEFAULT '',
    cadence TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT 'at_least',
    last_measured_at TIMESTAMPTZ,
    UNIQUE (goal_id, name)
);
CREATE INDEX IF NOT EXISTS goal_metrics_goal_idx ON goal_metrics (goal_id);

CREATE TABLE IF NOT EXISTS goal_progress (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID NOT NULL REFERENCES {GOALS_TABLE}(id) ON DELETE CASCADE,
    metric_name TEXT,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    value DOUBLE PRECISION,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS goal_progress_goal_idx ON goal_progress (goal_id);

CREATE TABLE IF NOT EXISTS goal_asks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID NOT NULL REFERENCES {GOALS_TABLE}(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    metric_name TEXT,
    question TEXT NOT NULL,
    asked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS goal_asks_user_idx ON goal_asks (user_id, asked_at);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class GoalRecord:
    """One prioritised registry goal (metrics fetched separately)."""

    id: str
    owner_user_id: str
    visibility: str
    title: str
    description: str
    priority: str
    status: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    deadline: Optional[datetime]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "status": self.status,
            "deadline": self.deadline.isoformat() if self.deadline else None,
        }


@dataclass(frozen=True)
class MeasurementGap:
    """A goal the proactive monitor should ask about, and why."""

    goal: GoalRecord
    reason: str  # "no_metric" | "unmeasured_target" | "stale"
    metric_name: Optional[str] = None


def _resolve_visibility(principal: Principal, visibility: Optional[str]) -> str:
    """Map a requested ``shared``/``private`` intent onto a concrete C2 tag.

    ``None``/``"private"`` become the caller's own ``private:<user_id>`` (a
    principal may only create rows private to itself); ``shared`` or a
    fully-qualified ``private:<u>`` is validated and passed through.
    """
    if visibility is None or visibility == "private":
        return principal.private_visibility
    return normalize_visibility(visibility)


def _staleness_key(goal: GoalRecord) -> tuple:
    """Tie-break key for equal-priority goals: soonest deadline, then oldest.

    A goal with a deadline outranks one without; among deadlines the earliest
    wins; ties fall back to the oldest ``created_at`` (longest-waiting first).
    """
    has_deadline = goal.deadline is not None
    deadline = goal.deadline or datetime.max.replace(tzinfo=timezone.utc)
    created = goal.created_at or datetime.max.replace(tzinfo=timezone.utc)
    return (0 if has_deadline else 1, deadline, created)


def order_goals(goals: List[GoalRecord]) -> List[GoalRecord]:
    """Order goals by priority band, then staleness/deadline (contract-free)."""
    return sorted(goals, key=lambda g: (priority_rank(g.priority), _staleness_key(g)))


def schedule_turn_budget(
    goals: List[GoalRecord],
    total_budget: int,
) -> Dict[str, int]:
    """Split ``total_budget`` turns across ``goals`` by priority weight.

    Higher-priority goals receive at least as many turns as lower-priority
    ones (the scheduling invariant). Allocation is proportional to
    :func:`hermes_cli.goals.priority_weight` using largest-remainder rounding;
    any leftover turns go to goals in :func:`order_goals` order so ties break
    by deadline/staleness. Returns ``{goal_id: turns}``; goals that receive
    zero are still present with ``0`` so callers see the full slate.
    """
    if total_budget <= 0 or not goals:
        return {g.id: 0 for g in goals}

    ordered = order_goals(goals)
    weights = {g.id: priority_weight(g.priority) for g in ordered}
    total_weight = sum(weights.values())

    allotment: Dict[str, int] = {}
    remainders: List[Tuple[float, int, str]] = []
    assigned = 0
    for index, g in enumerate(ordered):
        exact = total_budget * weights[g.id] / total_weight
        base = int(exact)
        allotment[g.id] = base
        assigned += base
        remainders.append((exact - base, index, g.id))

    leftover = total_budget - assigned
    # Largest fractional remainder first; ties keep order_goals ordering.
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for _, _, goal_id in remainders[:leftover]:
        allotment[goal_id] += 1
    return allotment


class GoalRegistryStore:
    """Async CRUD + measurement over the C2-scoped registry tables.

    The store never opens a raw connection itself — it always routes through
    the injected contract-C3 :class:`~hermes_cli.datastore.SupabaseAppStore`,
    whose ``mode`` selects the ``app_dev`` / ``app_prod`` schema.
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
        """Create the registry tables and the ``goals`` RLS policy (idempotent)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await conn.execute(_SCHEMA_SQL)
            await apply_scope_rls(conn, GOALS_TABLE)
        finally:
            if own:
                await conn.close()

    # -- goals -------------------------------------------------------------

    async def create_goal(
        self,
        principal: Principal,
        title: str,
        *,
        description: str = "",
        priority: str = DEFAULT_GOAL_PRIORITY,
        visibility: Optional[str] = None,
        deadline: Optional[datetime] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalRecord:
        """Create a goal owned by ``principal`` and return it."""
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Cannot create a goal with an empty title")
        resolved_visibility = _resolve_visibility(principal, visibility)
        resolved_priority = normalize_priority(priority)

        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {GOALS_TABLE}
                    (owner_user_id, visibility, title, description, priority,
                     deadline)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, owner_user_id, visibility, title, description,
                          priority, status, created_at, updated_at, deadline
                """,
                principal.user_id,
                resolved_visibility,
                clean_title,
                description or "",
                resolved_priority,
                deadline,
            )
            return _row_to_goal(row)
        finally:
            if own:
                await conn.close()

    async def get_goal(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[GoalRecord]:
        """Return the goal if ``principal`` may read it (contract C2), else None."""
        predicate = scope_filter(principal, start_index=2)
        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                SELECT id, owner_user_id, visibility, title, description,
                       priority, status, created_at, updated_at, deadline
                FROM {GOALS_TABLE}
                WHERE id = $1 AND {predicate.sql}
                """,
                goal_id,
                *predicate.params,
            )
            return _row_to_goal(row) if row is not None else None
        finally:
            if own:
                await conn.close()

    async def list_goals(
        self,
        principal: Principal,
        *,
        status: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[GoalRecord]:
        """List goals ``principal`` may read, ordered by priority then staleness.

        A non-owner sees ``shared`` goals plus its own ``private`` goals; the
        owner sees everything (contract C2). Pass ``status`` to filter.
        """
        clauses: List[str] = []
        params: List[object] = []
        next_index = 1
        if status is not None:
            clauses.append(f"status = ${next_index}")
            params.append(status)
            next_index += 1
        predicate = scope_filter(principal, start_index=next_index)
        clauses.append(predicate.sql)
        params.extend(predicate.params)
        where = " AND ".join(clauses)

        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT id, owner_user_id, visibility, title, description,
                       priority, status, created_at, updated_at, deadline
                FROM {GOALS_TABLE}
                WHERE {where}
                """,
                *params,
            )
            return order_goals([_row_to_goal(r) for r in rows])
        finally:
            if own:
                await conn.close()

    async def set_priority(
        self,
        principal: Principal,
        goal_id: str,
        priority: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalRecord:
        """Change a goal's priority (must be readable by ``principal``)."""
        return await self._update_goal_field(
            principal, goal_id, "priority", normalize_priority(priority),
            connection=connection,
        )

    async def set_status(
        self,
        principal: Principal,
        goal_id: str,
        status: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalRecord:
        """Change a goal's lifecycle status (must be readable by ``principal``)."""
        if status not in GOAL_STATUSES:
            raise ValueError(f"Unknown goal status: {status!r}")
        return await self._update_goal_field(
            principal, goal_id, "status", status, connection=connection,
        )

    async def _update_goal_field(
        self,
        principal: Principal,
        goal_id: str,
        column: str,
        value: object,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalRecord:
        predicate = scope_filter(principal, start_index=3)
        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                UPDATE {GOALS_TABLE}
                SET {column} = $2, updated_at = NOW()
                WHERE id = $1 AND {predicate.sql}
                RETURNING id, owner_user_id, visibility, title, description,
                          priority, status, created_at, updated_at, deadline
                """,
                goal_id,
                value,
                *predicate.params,
            )
            if row is None:
                raise PermissionError(
                    f"Goal {goal_id} not found or not writable by "
                    f"{principal.user_id}"
                )
            return _row_to_goal(row)
        finally:
            if own:
                await conn.close()

    # -- metrics -----------------------------------------------------------

    async def _require_readable(
        self,
        principal: Principal,
        goal_id: str,
        conn: "asyncpg.Connection",
    ) -> GoalRecord:
        goal = await self.get_goal(principal, goal_id, connection=conn)
        if goal is None:
            raise PermissionError(
                f"Goal {goal_id} not found or not visible to {principal.user_id}"
            )
        return goal

    async def add_metric(
        self,
        principal: Principal,
        goal_id: str,
        metric: GoalMetric,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalMetric:
        """Attach (or replace by name) a metric to a goal ``principal`` can read."""
        if not metric.name.strip():
            raise ValueError("GoalMetric.name is required")
        own = connection is None
        conn = connection or await self._connect()
        try:
            await self._require_readable(principal, goal_id, conn)
            measured_at = _utcnow() if metric.is_measurable() else None
            row = await conn.fetchrow(
                """
                INSERT INTO goal_metrics
                    (goal_id, name, target, current, unit, source_query,
                     cadence, direction, last_measured_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (goal_id, name) DO UPDATE SET
                    target = EXCLUDED.target,
                    current = EXCLUDED.current,
                    unit = EXCLUDED.unit,
                    source_query = EXCLUDED.source_query,
                    cadence = EXCLUDED.cadence,
                    direction = EXCLUDED.direction,
                    last_measured_at = EXCLUDED.last_measured_at
                RETURNING name, target, current, unit, source_query, cadence,
                          direction
                """,
                goal_id,
                metric.name.strip(),
                metric.target,
                metric.current,
                metric.unit,
                metric.source_query,
                metric.cadence,
                metric.direction,
                measured_at,
            )
            return _row_to_metric(row)
        finally:
            if own:
                await conn.close()

    async def list_metrics(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[GoalMetric]:
        """List a goal's metrics — empty if the goal isn't readable (C2)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            if await self.get_goal(principal, goal_id, connection=conn) is None:
                return []
            rows = await conn.fetch(
                """
                SELECT name, target, current, unit, source_query, cadence,
                       direction
                FROM goal_metrics WHERE goal_id = $1 ORDER BY name
                """,
                goal_id,
            )
            return [_row_to_metric(r) for r in rows]
        finally:
            if own:
                await conn.close()

    async def set_metric_value(
        self,
        principal: Principal,
        goal_id: str,
        name: str,
        current: float,
        *,
        note: str = "",
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalMetric:
        """Update a metric's ``current`` + ``last_measured_at`` and log progress."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await self._require_readable(principal, goal_id, conn)
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE goal_metrics
                    SET current = $3, last_measured_at = NOW()
                    WHERE goal_id = $1 AND name = $2
                    RETURNING name, target, current, unit, source_query,
                              cadence, direction
                    """,
                    goal_id,
                    name,
                    float(current),
                )
                if row is None:
                    raise KeyError(f"Metric {name!r} not found on goal {goal_id}")
                await conn.execute(
                    """
                    INSERT INTO goal_progress (goal_id, metric_name, value, note)
                    VALUES ($1, $2, $3, $4)
                    """,
                    goal_id,
                    name,
                    float(current),
                    note,
                )
            return _row_to_metric(row)
        finally:
            if own:
                await conn.close()

    async def set_metric_target(
        self,
        principal: Principal,
        goal_id: str,
        name: str,
        target: float,
        *,
        unit: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalMetric:
        """Set a previously-unmeasured metric's target (monitor answer path)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await self._require_readable(principal, goal_id, conn)
            row = await conn.fetchrow(
                """
                UPDATE goal_metrics
                SET target = $3,
                    unit = COALESCE($4, unit),
                    last_measured_at = NOW()
                WHERE goal_id = $1 AND name = $2
                RETURNING name, target, current, unit, source_query, cadence,
                          direction
                """,
                goal_id,
                name,
                float(target),
                unit,
            )
            if row is None:
                raise KeyError(f"Metric {name!r} not found on goal {goal_id}")
            return _row_to_metric(row)
        finally:
            if own:
                await conn.close()

    async def record_progress(
        self,
        principal: Principal,
        goal_id: str,
        *,
        value: Optional[float] = None,
        note: str = "",
        metric_name: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        """Append a free-form progress entry to a goal ``principal`` can read."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await self._require_readable(principal, goal_id, conn)
            await conn.execute(
                """
                INSERT INTO goal_progress (goal_id, metric_name, value, note)
                VALUES ($1, $2, $3, $4)
                """,
                goal_id,
                metric_name,
                value,
                note,
            )
        finally:
            if own:
                await conn.close()

    async def list_progress(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[dict]:
        """Return the progress history (oldest first) — empty if unreadable."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            if await self.get_goal(principal, goal_id, connection=conn) is None:
                return []
            rows = await conn.fetch(
                """
                SELECT metric_name, ts, value, note
                FROM goal_progress WHERE goal_id = $1 ORDER BY ts, id
                """,
                goal_id,
            )
            return [
                {
                    "metric_name": r["metric_name"],
                    "ts": r["ts"],
                    "value": r["value"],
                    "note": r["note"],
                }
                for r in rows
            ]
        finally:
            if own:
                await conn.close()

    # -- proactive-measurement support ------------------------------------

    async def measurement_gaps(
        self,
        principal: Principal,
        *,
        now: Optional[datetime] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[MeasurementGap]:
        """Find active goals that need a measurement ask (contract C2 scoped).

        A goal is a gap when it has **no metric**, has a metric with **no
        target** (unmeasured), or a measurable metric whose ``last_measured_at``
        is older than its ``cadence`` (stale). Only goals ``principal`` may read
        are considered; the monitor then rides contract C6 before asking.
        """
        now = now or _utcnow()
        own = connection is None
        conn = connection or await self._connect()
        try:
            goals = await self.list_goals(
                principal, status=_ACTIVE_STATUS, connection=conn
            )
            gaps: List[MeasurementGap] = []
            for goal in goals:
                rows = await conn.fetch(
                    """
                    SELECT name, target, cadence, last_measured_at
                    FROM goal_metrics WHERE goal_id = $1 ORDER BY name
                    """,
                    goal.id,
                )
                if not rows:
                    gaps.append(MeasurementGap(goal, "no_metric"))
                    continue
                for r in rows:
                    if r["target"] is None:
                        gaps.append(
                            MeasurementGap(goal, "unmeasured_target", r["name"])
                        )
                    elif _is_stale(r["cadence"], r["last_measured_at"], now):
                        gaps.append(MeasurementGap(goal, "stale", r["name"]))
            return gaps
        finally:
            if own:
                await conn.close()

    async def log_ask(
        self,
        principal: Principal,
        goal_id: str,
        question: str,
        *,
        metric_name: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        """Record that a proactive measurement ask was sent (rate-limit + audit)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await self._require_readable(principal, goal_id, conn)
            await conn.execute(
                """
                INSERT INTO goal_asks (goal_id, user_id, metric_name, question)
                VALUES ($1, $2, $3, $4)
                """,
                goal_id,
                principal.user_id,
                metric_name,
                question,
            )
        finally:
            if own:
                await conn.close()

    async def last_ask_at(
        self,
        user_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[datetime]:
        """Most recent proactive-ask timestamp for a user (rate-limit input)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            return await conn.fetchval(
                "SELECT MAX(asked_at) FROM goal_asks WHERE user_id = $1",
                user_id,
            )
        finally:
            if own:
                await conn.close()


# --- cadence parsing --------------------------------------------------------

_CADENCE_UNITS = {
    "m": timedelta(minutes=1),
    "min": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "hr": timedelta(hours=1),
    "d": timedelta(days=1),
    "day": timedelta(days=1),
    "w": timedelta(weeks=1),
    "wk": timedelta(weeks=1),
}


def parse_cadence(cadence: str) -> Optional[timedelta]:
    """Parse a compact cadence like ``"1d"`` / ``"12h"`` / ``"30m"``.

    Returns ``None`` for an empty/unparseable cadence (treated as "no cadence"
    → never stale on a time basis).
    """
    text = (cadence or "").strip().lower()
    if not text:
        return None
    number = ""
    unit = ""
    for ch in text:
        if ch.isdigit() or ch == ".":
            number += ch
        else:
            unit += ch
    if not number:
        return None
    delta = _CADENCE_UNITS.get(unit.strip())
    if delta is None:
        return None
    try:
        return delta * float(number)
    except ValueError:
        return None


def _is_stale(
    cadence: Optional[str],
    last_measured_at: Optional[datetime],
    now: datetime,
) -> bool:
    interval = parse_cadence(cadence or "")
    if interval is None:
        return False
    if last_measured_at is None:
        return True
    return (now - last_measured_at) > interval


def _row_to_goal(row: "asyncpg.Record") -> GoalRecord:
    return GoalRecord(
        id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        visibility=str(row["visibility"]),
        title=str(row["title"]),
        description=str(row["description"] or ""),
        priority=str(row["priority"]),
        status=str(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deadline=row["deadline"],
    )


def _row_to_metric(row: "asyncpg.Record") -> GoalMetric:
    target = row["target"]
    return GoalMetric(
        name=str(row["name"]),
        target=(float(target) if target is not None else None),
        current=float(row["current"] or 0.0),
        unit=str(row["unit"] or ""),
        source_query=str(row["source_query"] or ""),
        cadence=str(row["cadence"] or ""),
        direction=str(row["direction"] or "at_least"),
    )


__all__ = [
    "GOALS_TABLE",
    "GOAL_STATUSES",
    "GoalRecord",
    "GoalRegistryStore",
    "MeasurementGap",
    "SHARED",
    "order_goals",
    "parse_cadence",
    "schedule_turn_budget",
]
