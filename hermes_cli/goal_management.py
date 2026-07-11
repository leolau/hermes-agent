"""Scoped goal integration over the existing goal, memory, task, and tool stores.

The service is the single FG-09 management layer used by channel, Telegram,
web, and MCP adapters. It stores only typed links; the linked records remain in
their existing FG-04/05/06/07 registries. Goal context is returned as ordinary
tool-result/message content and never changes a live system prompt or toolset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Mapping, Optional, cast

from hermes_cli.access import Principal, apply_scope_rls, parse_private_owner
from hermes_cli.changes import ChangeLog, initialize_changes
from hermes_cli.datastore import StoreMode, SupabaseAppStore, get_store
from hermes_cli.goal_registry import GoalRecord, GoalRegistryStore
from hermes_cli.goals import GoalMetric, metrics_all_achieved
from hermes_cli.task_registry import TaskRecord, TaskRegistryStore
from hermes_cli.tools_registry import Tool, ToolRegistry
from plugins.memory.supabase_pgvector.store import MemoryRecord, PgvectorMemoryStore

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.consent import ApprovalCallback
    from hermes_cli.datastore import SessionOrigin


ResourceKind = Literal["memory", "task", "tool"]
GoalSurface = Literal["channel", "telegram", "web", "mcp"]
RESOURCE_KINDS: tuple[ResourceKind, ...] = ("memory", "task", "tool")
GOAL_LINKS_TABLE = "goal_links"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {GOAL_LINKS_TABLE} (
    goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    resource_kind TEXT NOT NULL
        CHECK (resource_kind IN ('memory', 'task', 'tool')),
    resource_ref TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (goal_id, resource_kind, resource_ref)
);
CREATE INDEX IF NOT EXISTS {GOAL_LINKS_TABLE}_goal_idx
    ON {GOAL_LINKS_TABLE} (goal_id, created_at);
CREATE INDEX IF NOT EXISTS {GOAL_LINKS_TABLE}_resource_idx
    ON {GOAL_LINKS_TABLE} (resource_kind, resource_ref);
"""


@dataclass(frozen=True)
class GoalLink:
    goal_id: str
    resource_kind: ResourceKind
    resource_ref: str
    owner_user_id: str
    visibility: str
    created_at: Optional[datetime]

    def as_dict(self) -> dict[str, object]:
        return {
            "goal_id": self.goal_id,
            "resource_kind": self.resource_kind,
            "resource_ref": self.resource_ref,
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "created_at": (
                self.created_at.isoformat() if self.created_at is not None else None
            ),
        }


@dataclass(frozen=True)
class LinkedResource:
    link: GoalLink
    resource: MemoryRecord | TaskRecord | Tool

    def as_dict(self) -> dict[str, object]:
        return {
            "link": self.link.as_dict(),
            "resource": self.resource.as_dict(),
        }


@dataclass(frozen=True)
class GoalContext:
    goal: GoalRecord
    metrics: tuple[GoalMetric, ...]
    progress: tuple[dict[str, object], ...]
    resources: tuple[LinkedResource, ...]

    def as_dict(self) -> dict[str, object]:
        progress = []
        for item in self.progress:
            normalized = dict(item)
            timestamp = normalized.get("ts")
            if isinstance(timestamp, datetime):
                normalized["ts"] = timestamp.isoformat()
            progress.append(normalized)
        return {
            "goal": self.goal.as_dict(),
            "metrics": [metric.to_dict() for metric in self.metrics],
            "progress": progress,
            "resources": [resource.as_dict() for resource in self.resources],
        }

    def as_tool_result(self) -> str:
        """Render cache-safe appended content for a tool result or message."""
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)


class GoalManagementService:
    """One scoped management layer for every FG-09 frontend."""

    def __init__(
        self,
        store: SupabaseAppStore,
        *,
        audit_store: Optional[SupabaseAppStore] = None,
        approval_callback: Optional["ApprovalCallback"] = None,
    ) -> None:
        self._store = store
        self._audit_store = audit_store
        self._approval_callback = approval_callback
        self.goals = GoalRegistryStore(store)
        self.memory = PgvectorMemoryStore(store)
        self.tasks = TaskRegistryStore(store)
        self.tools = ToolRegistry(store)

    @classmethod
    def from_config(
        cls,
        *,
        mode: Optional[StoreMode] = None,
        source: Optional["SessionOrigin"] = None,
        config: Optional[Mapping[str, object]] = None,
        approval_callback: Optional["ApprovalCallback"] = None,
    ) -> "GoalManagementService":
        return cls(
            get_store("supabase-app", mode, source=source, config=config),
            audit_store=get_store("supabase-app", "prod", config=config),
            approval_callback=approval_callback,
        )

    @property
    def mode(self) -> str:
        return self._store.mode

    async def initialize(self) -> None:
        connection = await self._store.connect()
        try:
            await connection.execute(
                f'CREATE SCHEMA IF NOT EXISTS "{self._store.schema}"'
            )
            await self.goals.initialize(connection=connection)
            await self.memory.initialize(connection=connection)
            await self.tasks.initialize(connection=connection)
            await self.tools.initialize(connection=connection)
            await connection.execute(_SCHEMA_SQL)
            await apply_scope_rls(connection, GOAL_LINKS_TABLE)
        finally:
            await connection.close()

        if self._audit_store is not None:
            audit = await self._audit_store.connect()
            try:
                await initialize_changes(audit)
            finally:
                await audit.close()

    async def create_goal(
        self,
        principal: Principal,
        title: str,
        *,
        description: str = "",
        priority: str = "medium",
        visibility: Optional[str] = None,
        surface: GoalSurface,
    ) -> GoalRecord:
        self._assert_writer(principal)
        private_owner = parse_private_owner(visibility or "")
        if (
            private_owner is not None
            and private_owner != principal.user_id
            and not principal.is_owner
        ):
            raise PermissionError("cannot create a goal private to another user")
        goal = await self.goals.create_goal(
            principal,
            title,
            description=description,
            priority=priority,
            visibility=visibility,
        )
        await self._record_change(
            principal,
            action="goal.create",
            target_ref=goal.id,
            visibility=goal.visibility,
            payload={"surface": surface, "goal": goal.as_dict()},
        )
        return goal

    async def list_goals(
        self,
        principal: Principal,
        *,
        status: Optional[str] = None,
    ) -> list[GoalRecord]:
        return await self.goals.list_goals(principal, status=status)

    async def prioritise(
        self,
        principal: Principal,
        goal_id: str,
        priority: str,
        *,
        surface: GoalSurface,
    ) -> GoalRecord:
        before = await self._require_goal_writable(principal, goal_id)
        goal = await self.goals.set_priority(principal, goal_id, priority)
        await self._record_change(
            principal,
            action="goal.prioritise",
            target_ref=goal.id,
            visibility=goal.visibility,
            payload={
                "surface": surface,
                "before": before.priority,
                "after": goal.priority,
            },
        )
        return goal

    async def link(
        self,
        principal: Principal,
        goal_id: str,
        resource_kind: ResourceKind,
        resource_ref: str,
        *,
        surface: GoalSurface,
    ) -> GoalLink:
        if resource_kind not in RESOURCE_KINDS:
            raise ValueError(f"unknown resource kind: {resource_kind!r}")
        clean_ref = resource_ref.strip()
        if not clean_ref:
            raise ValueError("resource_ref is required")

        goal = await self._require_goal_writable(principal, goal_id)
        connection = await self._store.connect()
        try:
            resource = await self._load_resource(
                principal,
                resource_kind,
                clean_ref,
                connection=connection,
            )
            if resource is None:
                raise LookupError(
                    f"{resource_kind} {clean_ref!r} not found or not visible"
                )
            link_visibility = _link_visibility(
                goal.visibility,
                resource.visibility,
            )
            row = await connection.fetchrow(
                f"""
                INSERT INTO {GOAL_LINKS_TABLE}
                    (goal_id, resource_kind, resource_ref, owner_user_id,
                     visibility)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (goal_id, resource_kind, resource_ref)
                DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    visibility = EXCLUDED.visibility
                RETURNING goal_id, resource_kind, resource_ref, owner_user_id,
                          visibility, created_at
                """,
                goal_id,
                resource_kind,
                clean_ref,
                goal.owner_user_id,
                link_visibility,
            )
        finally:
            await connection.close()
        link = _row_to_link(row)
        await self._record_change(
            principal,
            action="goal.link",
            target_ref=goal.id,
            visibility=goal.visibility,
            payload={"surface": surface, "link": link.as_dict()},
        )
        return link

    async def list_links(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> list[GoalLink]:
        goal = await self.goals.get_goal(
            principal,
            goal_id,
            connection=connection,
        )
        if goal is None:
            return []
        own = connection is None
        conn = connection or await self._store.connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT goal_id, resource_kind, resource_ref, owner_user_id,
                       visibility, created_at
                FROM {GOAL_LINKS_TABLE}
                WHERE goal_id = $1
                ORDER BY created_at, resource_kind, resource_ref
                """,
                goal_id,
            )
            visible: list[GoalLink] = []
            for row in rows:
                link = _row_to_link(row)
                resource = await self._load_resource(
                    principal,
                    link.resource_kind,
                    link.resource_ref,
                    connection=conn,
                )
                if resource is not None:
                    visible.append(link)
            return visible
        finally:
            if own:
                await conn.close()

    async def advance_task(
        self,
        principal: Principal,
        goal_id: str,
        task_id: str,
        to_state: str,
        *,
        surface: GoalSurface,
    ) -> TaskRecord:
        connection = await self._store.connect()
        try:
            async with connection.transaction():
                goal = await self._require_goal_writable(
                    principal,
                    goal_id,
                    connection=connection,
                )
                task = await self.tasks.get_task(
                    principal,
                    task_id,
                    connection=connection,
                )
                if task is None:
                    raise LookupError("task not found or not visible")
                self._assert_resource_writer(principal, task.owner_user_id, "task")
                await self._require_link(
                    goal_id,
                    "task",
                    task_id,
                    connection=connection,
                )
                updated = await self.tasks.transition(
                    principal,
                    task_id,
                    to_state,
                    actor=principal.user_id,
                    connection=connection,
                )
                await self.goals.record_progress(
                    principal,
                    goal_id,
                    note=(
                        f"{surface} advanced linked task {task_id} "
                        f"to {updated.current_state}"
                    ),
                    connection=connection,
                )
                if updated.status == "completed":
                    await self._close_if_complete(
                        principal,
                        goal_id,
                        connection=connection,
                    )
        finally:
            await connection.close()
        await self._record_change(
            principal,
            action="goal.advance_task",
            target_ref=goal.id,
            visibility=goal.visibility,
            payload={
                "surface": surface,
                "task_id": task_id,
                "from_state": task.current_state,
                "to_state": updated.current_state,
            },
        )
        return updated

    async def advance_metric(
        self,
        principal: Principal,
        goal_id: str,
        metric_name: str,
        value: float,
        *,
        note: str = "",
        surface: GoalSurface,
    ) -> GoalMetric:
        connection = await self._store.connect()
        try:
            async with connection.transaction():
                goal = await self._require_goal_writable(
                    principal,
                    goal_id,
                    connection=connection,
                )
                metric = await self.goals.set_metric_value(
                    principal,
                    goal_id,
                    metric_name,
                    value,
                    note=note or f"{surface} metric update",
                    connection=connection,
                )
                metrics = await self.goals.list_metrics(
                    principal,
                    goal_id,
                    connection=connection,
                )
                if metrics_all_achieved(metrics):
                    await self.goals.set_status(
                        principal,
                        goal_id,
                        "done",
                        connection=connection,
                    )
        finally:
            await connection.close()
        await self._record_change(
            principal,
            action="goal.advance_metric",
            target_ref=goal.id,
            visibility=goal.visibility,
            payload={
                "surface": surface,
                "metric_name": metric_name,
                "value": value,
            },
        )
        return metric

    async def record_progress(
        self,
        principal: Principal,
        goal_id: str,
        note: str,
        *,
        surface: GoalSurface,
    ) -> None:
        goal = await self._require_goal_writable(principal, goal_id)
        await self.goals.record_progress(
            principal,
            goal_id,
            note=f"{surface}: {note.strip()}",
        )
        await self._record_change(
            principal,
            action="goal.advance",
            target_ref=goal.id,
            visibility=goal.visibility,
            payload={"surface": surface, "note": note.strip()},
        )

    async def close_goal(
        self,
        principal: Principal,
        goal_id: str,
        *,
        surface: GoalSurface,
    ) -> GoalRecord:
        before = await self._require_goal_writable(principal, goal_id)
        goal = await self.goals.set_status(principal, goal_id, "done")
        await self._record_change(
            principal,
            action="goal.close",
            target_ref=goal.id,
            visibility=goal.visibility,
            payload={"surface": surface, "before": before.status, "after": "done"},
        )
        return goal

    async def assemble_context(
        self,
        principal: Principal,
        goal_id: str,
    ) -> GoalContext:
        goal = await self.goals.get_goal(principal, goal_id)
        if goal is None:
            raise LookupError("goal not found or not visible")
        metrics = tuple(await self.goals.list_metrics(principal, goal_id))
        progress = tuple(await self.goals.list_progress(principal, goal_id))
        links = await self.list_links(principal, goal_id)
        connection = await self._store.connect()
        try:
            resources = []
            for link in links:
                resource = await self._load_resource(
                    principal,
                    link.resource_kind,
                    link.resource_ref,
                    connection=connection,
                )
                if resource is not None:
                    resources.append(LinkedResource(link=link, resource=resource))
        finally:
            await connection.close()
        return GoalContext(
            goal=goal,
            metrics=metrics,
            progress=progress,
            resources=tuple(resources),
        )

    async def retire_tool(
        self,
        principal: Principal,
        tool_name: str,
        *,
        surface: GoalSurface,
    ) -> int:
        self._assert_writer(principal)
        tool = await self.tools.get_for_principal(principal, tool_name)
        if tool is None:
            raise LookupError("tool not found or not visible")
        self._assert_resource_writer(principal, tool.owner_user_id, "tool")

        from tools.approval import prompt_dangerous_approval

        decision = prompt_dangerous_approval(
            f"goal management retire tool {tool_name}",
            (
                f"delete tool {tool_name!r} and pause goals that depend on it "
                "(irreversible)"
            ),
            allow_permanent=False,
            approval_callback=self._approval_callback,
        )
        if decision not in {"once", "session"}:
            raise PermissionError("tool retirement approval denied")

        connection = await self._store.connect()
        try:
            rows = await connection.fetch(
                f"""
                SELECT goal_id FROM {GOAL_LINKS_TABLE}
                WHERE resource_kind = 'tool' AND resource_ref = $1
                """,
                tool_name,
            )
            goal_ids = [str(row["goal_id"]) for row in rows]
            async with connection.transaction():
                await self.tools.delete(
                    principal,
                    tool_name,
                    connection=connection,
                )
                for goal_id in goal_ids:
                    await connection.execute(
                        """
                        UPDATE goals
                        SET status = CASE
                            WHEN status = 'active' THEN 'paused'
                            ELSE status
                        END,
                        updated_at = NOW()
                        WHERE id = $1
                        """,
                        goal_id,
                    )
                    await connection.execute(
                        """
                        INSERT INTO goal_progress (goal_id, note)
                        VALUES ($1, $2)
                        """,
                        goal_id,
                        f"Linked tool retired: {tool_name}",
                    )
        finally:
            await connection.close()
        await self._record_change(
            principal,
            action="goal.retire_tool",
            target_ref=tool_name,
            visibility=tool.visibility,
            payload={
                "surface": surface,
                "tool": tool.as_dict(),
                "affected_goal_count": len(goal_ids),
            },
        )
        return len(goal_ids)

    async def _require_goal_writable(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GoalRecord:
        self._assert_writer(principal)
        goal = await self.goals.get_goal(
            principal,
            goal_id,
            connection=connection,
        )
        if goal is None:
            raise LookupError("goal not found or not visible")
        self._assert_resource_writer(principal, goal.owner_user_id, "goal")
        return goal

    async def _require_link(
        self,
        goal_id: str,
        resource_kind: ResourceKind,
        resource_ref: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        own = connection is None
        conn = connection or await self._store.connect()
        try:
            exists = await conn.fetchval(
                f"""
                SELECT EXISTS(
                    SELECT 1 FROM {GOAL_LINKS_TABLE}
                    WHERE goal_id = $1
                      AND resource_kind = $2
                      AND resource_ref = $3
                )
                """,
                goal_id,
                resource_kind,
                resource_ref,
            )
        finally:
            if own:
                await conn.close()
        if not exists:
            raise LookupError(
                f"{resource_kind} {resource_ref!r} is not linked to goal {goal_id}"
            )

    async def _load_resource(
        self,
        principal: Principal,
        resource_kind: ResourceKind,
        resource_ref: str,
        *,
        connection: "asyncpg.Connection",
    ) -> MemoryRecord | TaskRecord | Tool | None:
        if resource_kind == "memory":
            return await self.memory.get(
                principal,
                resource_ref,
                connection=connection,
            )
        if resource_kind == "task":
            return await self.tasks.get_task(
                principal,
                resource_ref,
                connection=connection,
            )
        return await self.tools.get_for_principal(
            principal,
            resource_ref,
            connection=connection,
        )

    async def _close_if_complete(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        metrics = await self.goals.list_metrics(
            principal,
            goal_id,
            connection=connection,
        )
        if metrics:
            if metrics_all_achieved(metrics):
                await self.goals.set_status(
                    principal,
                    goal_id,
                    "done",
                    connection=connection,
                )
            return
        links = await self.list_links(
            principal,
            goal_id,
            connection=connection,
        )
        task_refs = [
            link.resource_ref for link in links if link.resource_kind == "task"
        ]
        if not task_refs:
            return
        tasks = [
            await self.tasks.get_task(
                principal,
                task_id,
                connection=connection,
            )
            for task_id in task_refs
        ]
        if all(task is not None and task.status == "completed" for task in tasks):
            await self.goals.set_status(
                principal,
                goal_id,
                "done",
                connection=connection,
            )

    async def _record_change(
        self,
        principal: Principal,
        *,
        action: str,
        target_ref: str,
        visibility: str,
        payload: dict[str, object],
    ) -> None:
        if self._audit_store is None:
            return
        op = {
            "kind": "goal_management_event",
            "action": action,
            "target_ref": target_ref,
            "payload": payload,
        }
        await ChangeLog(self._audit_store).record(
            actor_user_id=principal.user_id,
            target_kind="data",
            op=op,
            inverse_op=None,
            reversible=False,
            action=action,
            target_ref=target_ref,
            mode=self.mode,
            visibility=visibility,
            payload=payload,
            approved=True,
        )

    @staticmethod
    def _assert_writer(principal: Principal) -> None:
        if principal.role == "viewer":
            raise PermissionError("viewer principals may not manage goals")

    @staticmethod
    def _assert_resource_writer(
        principal: Principal,
        owner_user_id: str,
        kind: str,
    ) -> None:
        if not principal.is_owner and principal.user_id != owner_user_id:
            raise PermissionError(
                f"{principal.user_id} may not mutate {kind} owned by {owner_user_id}"
            )


def _row_to_link(row: "asyncpg.Record") -> GoalLink:
    resource_kind = str(row["resource_kind"])
    if resource_kind not in RESOURCE_KINDS:
        raise ValueError(f"unknown stored resource kind: {resource_kind!r}")
    return GoalLink(
        goal_id=str(row["goal_id"]),
        resource_kind=cast(ResourceKind, resource_kind),
        resource_ref=str(row["resource_ref"]),
        owner_user_id=str(row["owner_user_id"]),
        visibility=str(row["visibility"]),
        created_at=row["created_at"],
    )


def _link_visibility(goal_visibility: str, resource_visibility: str) -> str:
    private_owners = {
        owner
        for owner in (
            parse_private_owner(goal_visibility),
            parse_private_owner(resource_visibility),
        )
        if owner is not None
    }
    if len(private_owners) > 1:
        raise PermissionError("cannot link resources with disjoint private scopes")
    if private_owners:
        return f"private:{private_owners.pop()}"
    return "shared"


__all__ = [
    "GOAL_LINKS_TABLE",
    "RESOURCE_KINDS",
    "GoalContext",
    "GoalLink",
    "GoalManagementService",
    "GoalSurface",
    "LinkedResource",
    "ResourceKind",
]
