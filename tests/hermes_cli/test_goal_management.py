"""Unit coverage for the shared FG-09 goal-management service."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from hermes_cli.access import Principal
from hermes_cli.goal_management import GoalManagementService, ResourceKind
from hermes_cli.goal_registry import GoalRecord
from hermes_cli.task_registry import TaskRecord


def _goal(owner: str = "alice") -> GoalRecord:
    return GoalRecord(
        id="00000000-0000-0000-0000-000000000001",
        owner_user_id=owner,
        visibility=f"private:{owner}",
        title="Ship integration",
        description="",
        priority="medium",
        status="active",
        created_at=None,
        updated_at=None,
        deadline=None,
    )


def _task(*, completed: bool = False) -> TaskRecord:
    return TaskRecord(
        id="task-1",
        owner_user_id="alice",
        visibility="private:alice",
        title="Finish task",
        description="",
        trigger_state="pending",
        completion_state="completed",
        current_state="completed" if completed else "pending",
        status="completed" if completed else "pending",
        origin="explicit",
        normalized_intent=None,
        created_at=None,
        updated_at=None,
    )


class _Transaction(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _Connection:
    def __init__(self) -> None:
        self.links: list[dict[str, object]] = []

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def fetchrow(self, query: str, *params: object) -> dict[str, object]:
        row = {
            "goal_id": params[0],
            "resource_kind": params[1],
            "resource_ref": params[2],
            "owner_user_id": params[3],
            "visibility": params[4],
            "created_at": datetime.now(timezone.utc),
        }
        self.links.append(row)
        return row

    async def fetch(self, query: str, *params: object) -> list[dict[str, object]]:
        return list(self.links)

    async def fetchval(self, query: str, *params: object) -> bool:
        return True

    async def close(self) -> None:
        return None


class _Store:
    mode = "prod"
    schema = "app_prod"

    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def connect(self) -> _Connection:
        return self.connection


class _Goals:
    def __init__(self) -> None:
        self.status = "active"
        self.progress: list[str] = []

    async def get_goal(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: _Connection | None = None,
    ) -> GoalRecord | None:
        return _goal()

    async def record_progress(
        self,
        principal: Principal,
        goal_id: str,
        *,
        note: str,
        connection: _Connection | None = None,
    ) -> None:
        self.progress.append(note)

    async def list_metrics(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: _Connection | None = None,
    ) -> list[object]:
        return []

    async def set_status(
        self,
        principal: Principal,
        goal_id: str,
        status: str,
        *,
        connection: _Connection | None = None,
    ) -> GoalRecord:
        self.status = status
        return _goal()


class _Resources:
    def __init__(self, visible: bool = True) -> None:
        self.visible = visible

    async def get(
        self,
        principal: Principal,
        resource_ref: str,
        *,
        connection: _Connection,
    ) -> object | None:
        return _VisibleResource() if self.visible else None

    async def get_task(
        self,
        principal: Principal,
        resource_ref: str,
        *,
        connection: _Connection | None = None,
    ) -> TaskRecord | None:
        return _task(completed=True) if self.visible else None

    async def get_for_principal(
        self,
        principal: Principal,
        resource_ref: str,
        *,
        connection: _Connection,
    ) -> object | None:
        return _VisibleResource() if self.visible else None

    async def transition(
        self,
        principal: Principal,
        task_id: str,
        to_state: str,
        *,
        actor: str,
        connection: _Connection,
    ) -> TaskRecord:
        return _task(completed=True)


@dataclass(frozen=True)
class _VisibleResource:
    visibility: str = "private:alice"


def _service(
    *,
    visible: bool = True,
) -> tuple[GoalManagementService, _Store, _Goals]:
    connection = _Connection()
    store = _Store(connection)
    goals = _Goals()
    service = GoalManagementService.__new__(GoalManagementService)
    service._store = store
    service._audit_store = None
    service._approval_callback = None
    service.goals = goals
    service.memory = _Resources(visible)
    service.tasks = _Resources(visible)
    service.tools = _Resources(visible)
    return service, store, goals


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_kind", ["memory", "task", "tool"])
async def test_link_validates_each_existing_scoped_resource(
    resource_kind: ResourceKind,
) -> None:
    service, _, _ = _service()
    alice = Principal(user_id="alice", display="Alice", role="member")

    link = await service.link(
        alice,
        _goal().id,
        resource_kind,
        "resource-1",
        surface="web",
    )

    assert link.resource_kind == resource_kind
    assert link.resource_ref == "resource-1"
    assert link.visibility == "private:alice"


@pytest.mark.asyncio
async def test_link_rejects_resource_hidden_by_c2_scope() -> None:
    service, _, _ = _service(visible=False)
    alice = Principal(user_id="alice", display="Alice", role="member")

    with pytest.raises(LookupError, match="not found or not visible"):
        await service.link(
            alice,
            _goal().id,
            "memory",
            "bob-private-memory",
            surface="web",
        )


@pytest.mark.asyncio
async def test_completed_linked_task_records_progress_and_closes_goal() -> None:
    service, store, goals = _service()
    connection = store.connection
    connection.links.append(
        {
            "goal_id": _goal().id,
            "resource_kind": "task",
            "resource_ref": "task-1",
            "owner_user_id": "alice",
            "visibility": "private:alice",
            "created_at": datetime.now(timezone.utc),
        }
    )
    alice = Principal(user_id="alice", display="Alice", role="member")

    task = await service.advance_task(
        alice,
        _goal().id,
        "task-1",
        "completed",
        surface="telegram",
    )

    assert task.status == "completed"
    assert goals.status == "done"
    assert goals.progress == [
        "telegram advanced linked task task-1 to completed"
    ]
