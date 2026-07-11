"""C2-scoped task progress and discovery coordination for FG-06.

Explicit session plans remain in :class:`tools.todo_tool.TodoStore`, and
multi-worker execution remains in the existing Kanban store. This module is
the app-layer coordination view for durable progress and approved discoveries;
it does not add another local task database or model tool.

Discovery consumes repeated intent signals from the FG-05 live memory store
through :class:`LiveMemoryIntentSignals`. Proposals are evaluated by the shared
C6 consent policy and delivered through an injected callback as appended
conversation content. Nothing here mutates a system prompt or toolset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import (
    TYPE_CHECKING,
    Callable,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
)

from hermes_cli.access import (
    Principal,
    apply_scope_rls,
    normalize_visibility,
    parse_private_owner,
    scope_filter,
)
from hermes_cli.changes import data_op
from hermes_cli.consent import (
    ApprovalCallback,
    ConsentDecision,
    ConsentPolicy,
    evaluate_approval,
    load_consent_policy,
)
from hermes_cli.datastore import get_store

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.datastore import SessionOrigin, StoreMode, SupabaseAppStore


TASKS_TABLE = "tasks"
TASK_STATUSES = ("pending", "in_progress", "completed", "cancelled")
TASK_ORIGINS = ("explicit", "discovered")

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TASKS_TABLE} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    trigger_state TEXT NOT NULL,
    completion_state TEXT NOT NULL,
    current_state TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    origin TEXT NOT NULL DEFAULT 'explicit',
    normalized_intent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_user_id, normalized_intent)
);
CREATE INDEX IF NOT EXISTS tasks_visibility_idx ON {TASKS_TABLE} (visibility);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON {TASKS_TABLE} (status);

CREATE TABLE IF NOT EXISTS task_progress_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES {TASKS_TABLE}(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    name TEXT NOT NULL,
    UNIQUE (task_id, ordinal),
    UNIQUE (task_id, name)
);
CREATE INDEX IF NOT EXISTS task_progress_states_task_idx
    ON task_progress_states (task_id, ordinal);

CREATE TABLE IF NOT EXISTS task_transitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES {TASKS_TABLE}(id) ON DELETE CASCADE,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS task_transitions_task_idx
    ON task_transitions (task_id, ts);

CREATE TABLE IF NOT EXISTS task_discovery_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    normalized_intent TEXT NOT NULL,
    signal_count INTEGER NOT NULL,
    decision TEXT NOT NULL,
    approval_mode TEXT NOT NULL,
    proposed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS task_discovery_proposals_lookup_idx
    ON task_discovery_proposals (owner_user_id, normalized_intent, proposed_at DESC);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _config_int(
    config: Optional[Mapping[str, object]],
    *keys: str,
    default: int,
) -> int:
    node: object = config or {}
    for key in keys:
        node = _as_mapping(node).get(key)
    if isinstance(node, bool):
        return default
    if isinstance(node, int):
        parsed = node
    elif isinstance(node, str):
        try:
            parsed = int(node)
        except ValueError:
            return default
    else:
        return default
    return parsed if parsed >= 0 else default


def _resolve_visibility(
    principal: Principal,
    visibility: Optional[str],
) -> str:
    if visibility is None or visibility == "private":
        return principal.private_visibility
    resolved = normalize_visibility(visibility)
    private_owner = parse_private_owner(resolved)
    if (
        private_owner is not None
        and private_owner != principal.user_id
        and not principal.is_owner
    ):
        raise PermissionError("cannot create a task private to another user")
    return resolved


def _validate_progress_states(
    trigger_state: str,
    completion_state: str,
    progress_states: tuple[str, ...],
) -> tuple[str, ...]:
    states = tuple(str(state).strip() for state in progress_states)
    if not states or any(not state for state in states):
        raise ValueError("progress_states must contain non-empty names")
    if len(set(states)) != len(states):
        raise ValueError("progress_states must be unique")
    if trigger_state not in states:
        raise ValueError("trigger_state must be in progress_states")
    if completion_state not in states:
        raise ValueError("completion_state must be in progress_states")
    if states.index(trigger_state) >= states.index(completion_state):
        raise ValueError("trigger_state must precede completion_state")
    return states


@dataclass(frozen=True)
class TaskSpec:
    title: str
    description: str
    trigger_state: str
    completion_state: str
    progress_states: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "description": self.description,
            "trigger_state": self.trigger_state,
            "completion_state": self.completion_state,
            "progress_states": list(self.progress_states),
        }


@dataclass(frozen=True)
class TaskRecord:
    id: str
    owner_user_id: str
    visibility: str
    title: str
    description: str
    trigger_state: str
    completion_state: str
    current_state: str
    status: str
    origin: str
    normalized_intent: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "title": self.title,
            "description": self.description,
            "trigger_state": self.trigger_state,
            "completion_state": self.completion_state,
            "current_state": self.current_state,
            "status": self.status,
            "origin": self.origin,
            "normalized_intent": self.normalized_intent,
        }


@dataclass(frozen=True)
class TaskTransition:
    from_state: str
    to_state: str
    ts: Optional[datetime]
    actor: str


@dataclass(frozen=True)
class DiscoveryOutcome:
    action: str
    normalized_intent: str = ""
    signal_count: int = 0
    spec: Optional[TaskSpec] = None
    decision: Optional[ConsentDecision] = None
    task: Optional[TaskRecord] = None


class IntentSignals(Protocol):
    async def record(
        self,
        principal: Principal,
        normalized_intent: str,
        *,
        source_session: Optional[str] = None,
    ) -> int: ...


class ChangeRecorder(Protocol):
    async def record(
        self,
        *,
        actor_user_id: str,
        target_kind: str,
        op: object,
        inverse_op: object,
        reversible: bool,
        action: str,
        target_ref: str,
        mode: str,
        visibility: str,
        payload: object,
        approved: bool,
    ) -> object: ...


class MemorySignalRecord(Protocol):
    @property
    def text(self) -> str: ...


class MemorySignalStore(Protocol):
    async def write(
        self,
        principal: Principal,
        text: str,
        *,
        kind: str,
        topic: Optional[str],
        visibility: Optional[str],
        source_session: Optional[str],
    ) -> object: ...

    async def query(
        self,
        principal: Principal,
        query_text: str,
        *,
        top_k: int,
        kind: Optional[str],
        topic: Optional[str],
    ) -> Sequence[MemorySignalRecord]: ...


class LiveMemoryIntentSignals:
    """Repeated-intent adapter over FG-05's live pgvector memory store."""

    def __init__(
        self,
        memory_store: MemorySignalStore,
        *,
        query_limit: int = 100,
    ) -> None:
        self._memory_store = memory_store
        self._query_limit = max(1, min(query_limit, 100))

    async def record(
        self,
        principal: Principal,
        normalized_intent: str,
        *,
        source_session: Optional[str] = None,
    ) -> int:
        await self._memory_store.write(
            principal,
            normalized_intent,
            kind="intent_signal",
            topic="task_discovery",
            visibility="private",
            source_session=source_session,
        )
        rows = await self._memory_store.query(
            principal,
            normalized_intent,
            top_k=self._query_limit,
            kind="intent_signal",
            topic="task_discovery",
        )
        return sum(1 for row in rows if row.text == normalized_intent)


class TaskRegistryStore:
    """Durable app-layer task progress, routed by a C3 Supabase store."""

    def __init__(self, store: "SupabaseAppStore") -> None:
        self._store = store

    @classmethod
    def from_config(
        cls,
        *,
        mode: Optional["StoreMode"] = None,
        source: Optional["SessionOrigin"] = None,
        config: Optional[Mapping[str, object]] = None,
    ) -> "TaskRegistryStore":
        store = get_store(
            "supabase-app",
            mode,
            source=source,
            config=config,
        )
        return cls(store)

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
        own = connection is None
        conn = connection or await self._connect()
        try:
            await conn.execute(_SCHEMA_SQL)
            await apply_scope_rls(conn, TASKS_TABLE)
            await apply_scope_rls(conn, "task_discovery_proposals")
        finally:
            if own:
                await conn.close()

    async def create_task(
        self,
        principal: Principal,
        spec: TaskSpec,
        *,
        visibility: Optional[str] = None,
        origin: Literal["explicit", "discovered"] = "explicit",
        normalized_intent: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> TaskRecord:
        if origin not in TASK_ORIGINS:
            raise ValueError(f"Unknown task origin: {origin!r}")
        states = _validate_progress_states(
            spec.trigger_state,
            spec.completion_state,
            spec.progress_states,
        )
        title = spec.title.strip()
        if not title:
            raise ValueError("task title cannot be empty")
        resolved_visibility = _resolve_visibility(principal, visibility)

        own = connection is None
        conn = connection or await self._connect()
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {TASKS_TABLE}
                        (owner_user_id, visibility, title, description,
                         trigger_state, completion_state, current_state,
                         status, origin, normalized_intent)
                    VALUES ($1, $2, $3, $4, $5, $6, $5, 'pending', $7, $8)
                    RETURNING *
                    """,
                    principal.user_id,
                    resolved_visibility,
                    title,
                    spec.description.strip(),
                    spec.trigger_state,
                    spec.completion_state,
                    origin,
                    normalized_intent,
                )
                await conn.executemany(
                    """
                    INSERT INTO task_progress_states (task_id, ordinal, name)
                    VALUES ($1, $2, $3)
                    """,
                    [
                        (row["id"], ordinal, state)
                        for ordinal, state in enumerate(states)
                    ],
                )
            return _row_to_task(row)
        finally:
            if own:
                await conn.close()

    async def get_task(
        self,
        principal: Principal,
        task_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[TaskRecord]:
        predicate = scope_filter(principal, start_index=2)
        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"SELECT * FROM {TASKS_TABLE} WHERE id = $1 AND {predicate.sql}",
                task_id,
                *predicate.params,
            )
            return _row_to_task(row) if row else None
        finally:
            if own:
                await conn.close()

    async def list_tasks(
        self,
        principal: Principal,
        *,
        status: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> list[TaskRecord]:
        clauses: list[str] = []
        params: list[object] = []
        next_index = 1
        if status is not None:
            if status not in TASK_STATUSES:
                raise ValueError(f"Unknown task status: {status!r}")
            clauses.append(f"status = ${next_index}")
            params.append(status)
            next_index += 1
        predicate = scope_filter(principal, start_index=next_index)
        clauses.append(predicate.sql)
        params.extend(predicate.params)

        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT * FROM {TASKS_TABLE}
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at ASC, id ASC
                """,
                *params,
            )
            return [_row_to_task(row) for row in rows]
        finally:
            if own:
                await conn.close()

    async def progress_states(
        self,
        principal: Principal,
        task_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> tuple[str, ...]:
        predicate = scope_filter(principal, column="t.visibility", start_index=2)
        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT s.name
                FROM task_progress_states s
                JOIN {TASKS_TABLE} t ON t.id = s.task_id
                WHERE t.id = $1 AND {predicate.sql}
                ORDER BY s.ordinal ASC
                """,
                task_id,
                *predicate.params,
            )
            return tuple(str(row["name"]) for row in rows)
        finally:
            if own:
                await conn.close()

    async def transition(
        self,
        principal: Principal,
        task_id: str,
        to_state: str,
        *,
        actor: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> TaskRecord:
        predicate = scope_filter(principal, column="t.visibility", start_index=2)
        own = connection is None
        conn = connection or await self._connect()
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""
                    SELECT t.*, s.ordinal AS current_ordinal
                    FROM {TASKS_TABLE} t
                    JOIN task_progress_states s
                      ON s.task_id = t.id AND s.name = t.current_state
                    WHERE t.id = $1 AND {predicate.sql}
                    FOR UPDATE OF t
                    """,
                    task_id,
                    *predicate.params,
                )
                if row is None:
                    raise LookupError("task not found or not visible")
                progress_rows = await conn.fetch(
                    """
                    SELECT name FROM task_progress_states
                    WHERE task_id = $1
                    ORDER BY ordinal
                    """,
                    task_id,
                )
                validate_progress_transition(
                    tuple(str(item["name"]) for item in progress_rows),
                    str(row["current_state"]),
                    to_state,
                )
                status = _status_for_state(
                    to_state,
                    trigger_state=str(row["trigger_state"]),
                    completion_state=str(row["completion_state"]),
                )
                updated = await conn.fetchrow(
                    f"""
                    UPDATE {TASKS_TABLE}
                    SET current_state = $2, status = $3, updated_at = NOW()
                    WHERE id = $1
                    RETURNING *
                    """,
                    task_id,
                    to_state,
                    status,
                )
                await conn.execute(
                    """
                    INSERT INTO task_transitions
                        (task_id, from_state, to_state, actor)
                    VALUES ($1, $2, $3, $4)
                    """,
                    task_id,
                    row["current_state"],
                    to_state,
                    actor or principal.user_id,
                )
            return _row_to_task(updated)
        finally:
            if own:
                await conn.close()

    async def transitions(
        self,
        principal: Principal,
        task_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> list[TaskTransition]:
        predicate = scope_filter(principal, column="t.visibility", start_index=2)
        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT x.from_state, x.to_state, x.ts, x.actor
                FROM task_transitions x
                JOIN {TASKS_TABLE} t ON t.id = x.task_id
                WHERE t.id = $1 AND {predicate.sql}
                ORDER BY x.ts ASC, x.id ASC
                """,
                task_id,
                *predicate.params,
            )
            return [
                TaskTransition(
                    from_state=str(row["from_state"]),
                    to_state=str(row["to_state"]),
                    ts=row["ts"],
                    actor=str(row["actor"]),
                )
                for row in rows
            ]
        finally:
            if own:
                await conn.close()

    async def find_discovered_task(
        self,
        principal: Principal,
        normalized_intent: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[TaskRecord]:
        predicate = scope_filter(principal, start_index=3)
        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                SELECT * FROM {TASKS_TABLE}
                WHERE owner_user_id = $1
                  AND normalized_intent = $2
                  AND {predicate.sql}
                LIMIT 1
                """,
                principal.user_id,
                normalized_intent,
                *predicate.params,
            )
            return _row_to_task(row) if row else None
        finally:
            if own:
                await conn.close()

    async def last_proposed_at(
        self,
        principal: Principal,
        normalized_intent: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[datetime]:
        predicate = scope_filter(principal, start_index=3)
        own = connection is None
        conn = connection or await self._connect()
        try:
            return await conn.fetchval(
                f"""
                SELECT proposed_at
                FROM task_discovery_proposals
                WHERE owner_user_id = $1 AND normalized_intent = $2
                  AND {predicate.sql}
                ORDER BY proposed_at DESC
                LIMIT 1
                """,
                principal.user_id,
                normalized_intent,
                *predicate.params,
            )
        finally:
            if own:
                await conn.close()

    async def record_proposal(
        self,
        principal: Principal,
        normalized_intent: str,
        signal_count: int,
        decision: ConsentDecision,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        own = connection is None
        conn = connection or await self._connect()
        try:
            await conn.execute(
                """
                INSERT INTO task_discovery_proposals
                    (owner_user_id, visibility, normalized_intent, signal_count,
                     decision, approval_mode)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                principal.user_id,
                principal.private_visibility,
                normalized_intent,
                signal_count,
                "approved" if decision.approved else "denied",
                decision.mode,
            )
        finally:
            if own:
                await conn.close()

    async def recent_auto_approvals(
        self,
        principal: Principal,
        *,
        since: datetime,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> int:
        predicate = scope_filter(principal, start_index=3)
        own = connection is None
        conn = connection or await self._connect()
        try:
            value = await conn.fetchval(
                f"""
                SELECT COUNT(*)
                FROM task_discovery_proposals
                WHERE owner_user_id = $1
                  AND decision = 'approved'
                  AND approval_mode = 'auto'
                  AND proposed_at >= $2
                  AND {predicate.sql}
                """,
                principal.user_id,
                since,
                *predicate.params,
            )
            return int(value or 0)
        finally:
            if own:
                await conn.close()


_COURTESY_PREFIX = re.compile(
    r"^(?:(?:please|can you|could you|would you|"
    r"i need you to|i want you to)\b[\s,;:!.-]*)+",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^\w\s-]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalize_intent(prompt: str) -> str:
    text = _COURTESY_PREFIX.sub("", str(prompt or "").strip().casefold())
    text = _NON_WORD.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip(" -")


def synthesize_task_spec(
    normalized_intent: str,
    *,
    signal_count: int,
) -> TaskSpec:
    intent = normalized_intent.strip()
    if not intent:
        raise ValueError("cannot synthesize a task from an empty intent")
    title = intent[:80].strip().capitalize()
    return TaskSpec(
        title=title,
        description=(
            f"Recurring intent observed {signal_count} times: {intent}"
        ),
        trigger_state="pending",
        completion_state="completed",
        progress_states=("pending", "in_progress", "completed"),
    )


def render_task_proposal(spec: TaskSpec, *, signal_count: int) -> str:
    states = " → ".join(spec.progress_states)
    return (
        "Task discovery proposal\n"
        f"Title: {spec.title}\n"
        f"Description: {spec.description}\n"
        f"Trigger: {spec.trigger_state}\n"
        f"Completion: {spec.completion_state}\n"
        f"Progress: {states}\n"
        f"Repeated intent count: {signal_count}"
    )


ProposalSink = Callable[[str], None]


class TaskDiscoveryEngine:
    """Count user intent signals, synthesize once, and approval-gate acceptance."""

    def __init__(
        self,
        store: TaskRegistryStore,
        signals: IntentSignals,
        *,
        threshold: int = 3,
        proposal_cooldown_seconds: int = 3600,
        policy: Optional[ConsentPolicy] = None,
        approval_callback: Optional[ApprovalCallback] = None,
        proposal_sink: Optional[ProposalSink] = None,
        change_recorder: Optional[ChangeRecorder] = None,
    ) -> None:
        if threshold < 2:
            raise ValueError("task discovery threshold must be at least 2")
        self._store = store
        self._signals = signals
        self._threshold = threshold
        self._proposal_cooldown = timedelta(
            seconds=max(0, proposal_cooldown_seconds)
        )
        self._policy = policy or load_consent_policy()
        self._approval_callback = approval_callback
        self._proposal_sink = proposal_sink
        self._change_recorder = change_recorder

    @classmethod
    def from_config(
        cls,
        store: TaskRegistryStore,
        signals: IntentSignals,
        *,
        config: Optional[Mapping[str, object]] = None,
        approval_callback: Optional[ApprovalCallback] = None,
        proposal_sink: Optional[ProposalSink] = None,
        change_recorder: Optional[ChangeRecorder] = None,
    ) -> "TaskDiscoveryEngine":
        return cls(
            store,
            signals,
            threshold=_config_int(
                config, "tasks", "discovery", "threshold", default=3
            ),
            proposal_cooldown_seconds=_config_int(
                config,
                "tasks",
                "discovery",
                "proposal_cooldown_seconds",
                default=3600,
            ),
            policy=load_consent_policy(
                dict(config) if config is not None else None
            ),
            approval_callback=approval_callback,
            proposal_sink=proposal_sink,
            change_recorder=change_recorder,
        )

    async def observe_prompt(
        self,
        principal: Principal,
        prompt: str,
        *,
        source_session: Optional[str] = None,
        origin: Literal["user", "discovered_task", "assistant", "system"] = "user",
        now: Optional[datetime] = None,
    ) -> DiscoveryOutcome:
        if origin != "user":
            return DiscoveryOutcome(action="ignored_origin")
        normalized = normalize_intent(prompt)
        if not normalized:
            return DiscoveryOutcome(action="ignored_empty")

        signal_count = await self._signals.record(
            principal,
            normalized,
            source_session=source_session,
        )
        if signal_count < self._threshold:
            return DiscoveryOutcome(
                action="below_threshold",
                normalized_intent=normalized,
                signal_count=signal_count,
            )

        existing = await self._store.find_discovered_task(principal, normalized)
        if existing is not None:
            return DiscoveryOutcome(
                action="already_tracked",
                normalized_intent=normalized,
                signal_count=signal_count,
                task=existing,
            )

        now = now or _utcnow()
        last_proposed = await self._store.last_proposed_at(principal, normalized)
        if (
            last_proposed is not None
            and now - last_proposed < self._proposal_cooldown
        ):
            return DiscoveryOutcome(
                action="proposal_suppressed",
                normalized_intent=normalized,
                signal_count=signal_count,
            )

        spec = synthesize_task_spec(normalized, signal_count=signal_count)
        proposal = render_task_proposal(spec, signal_count=signal_count)
        if self._proposal_sink is not None:
            self._proposal_sink(proposal)

        window = timedelta(seconds=self._policy.rate_limit_window_seconds)
        recent = await self._store.recent_auto_approvals(
            principal,
            since=now - window,
        )
        decision = evaluate_approval(
            self._policy,
            reversible=True,
            command="accept discovered task",
            description=proposal,
            now=now,
            recent_auto_approvals=recent,
            approval_callback=self._approval_callback,
        )
        await self._store.record_proposal(
            principal,
            normalized,
            signal_count,
            decision,
        )
        if not decision.approved:
            return DiscoveryOutcome(
                action="proposal_denied",
                normalized_intent=normalized,
                signal_count=signal_count,
                spec=spec,
                decision=decision,
            )

        task = await self._store.create_task(
            principal,
            spec,
            origin="discovered",
            normalized_intent=normalized,
        )
        if self._change_recorder is not None:
            forward, inverse = data_op(
                TASKS_TABLE,
                {"id": task.id},
                before=None,
                after=task.as_dict(),
            )
            await self._change_recorder.record(
                actor_user_id=principal.user_id,
                target_kind="data",
                op=forward,
                inverse_op=inverse,
                reversible=True,
                action="accept discovered task",
                target_ref=task.id,
                mode=self._store.mode,
                visibility=task.visibility,
                payload={
                    "origin": "discovered",
                    "normalized_intent": normalized,
                    "signal_count": signal_count,
                },
                approved=True,
            )
        return DiscoveryOutcome(
            action="task_accepted",
            normalized_intent=normalized,
            signal_count=signal_count,
            spec=spec,
            decision=decision,
            task=task,
        )


def _status_for_state(
    state: str,
    *,
    trigger_state: str,
    completion_state: str,
) -> str:
    if state == completion_state:
        return "completed"
    if state == trigger_state:
        return "pending"
    return "in_progress"


def validate_progress_transition(
    progress_states: Sequence[str],
    current_state: str,
    to_state: str,
) -> None:
    states = tuple(progress_states)
    if current_state not in states:
        raise ValueError(f"Unknown current progress state: {current_state!r}")
    if to_state not in states:
        raise ValueError(f"Unknown target progress state: {to_state!r}")
    current_index = states.index(current_state)
    target_index = states.index(to_state)
    if target_index != current_index + 1:
        raise ValueError(
            f"Invalid progress transition: {current_state!r} -> {to_state!r}"
        )


def _row_to_task(row: Mapping[str, object]) -> TaskRecord:
    return TaskRecord(
        id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        visibility=str(row["visibility"]),
        title=str(row["title"]),
        description=str(row["description"]),
        trigger_state=str(row["trigger_state"]),
        completion_state=str(row["completion_state"]),
        current_state=str(row["current_state"]),
        status=str(row["status"]),
        origin=str(row["origin"]),
        normalized_intent=(
            str(row["normalized_intent"])
            if row["normalized_intent"] is not None
            else None
        ),
        created_at=(
            row["created_at"] if isinstance(row["created_at"], datetime) else None
        ),
        updated_at=(
            row["updated_at"] if isinstance(row["updated_at"], datetime) else None
        ),
    )


__all__ = [
    "DiscoveryOutcome",
    "ChangeRecorder",
    "IntentSignals",
    "LiveMemoryIntentSignals",
    "TASKS_TABLE",
    "TASK_ORIGINS",
    "TASK_STATUSES",
    "TaskDiscoveryEngine",
    "TaskRecord",
    "TaskRegistryStore",
    "TaskSpec",
    "TaskTransition",
    "normalize_intent",
    "render_task_proposal",
    "synthesize_task_spec",
    "validate_progress_transition",
]
