"""Channel-neutral slash-command adapter for durable goal management."""

from __future__ import annotations

import shlex
from typing import Optional, cast

from hermes_cli.access import Principal
from hermes_cli.goal_management import (
    RESOURCE_KINDS,
    GoalManagementService,
    GoalSurface,
    ResourceKind,
)


USAGE = (
    "Usage: /goals list | create <title> | priority <goal_id> <priority> | "
    "link <goal_id> <memory|task|tool> <ref> | "
    "advance <goal_id> task <task_id> <state> | "
    "advance <goal_id> metric <name> <value> | "
    "advance <goal_id> note <text> | context <goal_id> | close <goal_id>"
)


async def execute_goal_command(
    service: GoalManagementService,
    principal: Principal,
    arguments: str,
    *,
    surface: GoalSurface,
) -> str:
    """Execute one durable-goal operation through the shared service."""
    try:
        parts = shlex.split(arguments)
    except ValueError as exc:
        return f"Invalid goal command: {exc}\n{USAGE}"
    if not parts:
        parts = ["list"]

    action = parts[0].lower()
    try:
        if action == "list" and len(parts) == 1:
            goals = await service.list_goals(principal)
            if not goals:
                return "No goals visible to you."
            return "\n".join(
                f"{goal.id} [{goal.priority}/{goal.status}] {goal.title}"
                for goal in goals
            )

        if action == "create" and len(parts) >= 2:
            goal = await service.create_goal(
                principal,
                " ".join(parts[1:]),
                surface=surface,
            )
            return f"Created goal {goal.id}: {goal.title}"

        if action in {"priority", "prioritise", "prioritize"} and len(parts) == 3:
            goal = await service.prioritise(
                principal,
                parts[1],
                parts[2],
                surface=surface,
            )
            return f"Goal {goal.id} priority is now {goal.priority}."

        if action == "link" and len(parts) == 4:
            kind = _resource_kind(parts[2])
            link = await service.link(
                principal,
                parts[1],
                kind,
                parts[3],
                surface=surface,
            )
            return (
                f"Linked {link.resource_kind} {link.resource_ref} "
                f"to goal {link.goal_id}."
            )

        if action == "advance" and len(parts) >= 4:
            goal_id = parts[1]
            target = parts[2].lower()
            if target == "task" and len(parts) == 5:
                task = await service.advance_task(
                    principal,
                    goal_id,
                    parts[3],
                    parts[4],
                    surface=surface,
                )
                return (
                    f"Task {task.id} advanced to {task.current_state}; "
                    f"status={task.status}."
                )
            if target == "metric" and len(parts) == 5:
                metric = await service.advance_metric(
                    principal,
                    goal_id,
                    parts[3],
                    float(parts[4]),
                    surface=surface,
                )
                return (
                    f"Metric {metric.name} is {metric.current}"
                    f"/{metric.target if metric.target is not None else '?'}."
                )
            if target == "note" and len(parts) >= 4:
                await service.record_progress(
                    principal,
                    goal_id,
                    " ".join(parts[3:]),
                    surface=surface,
                )
                return f"Recorded progress on goal {goal_id}."

        if action in {"context", "show"} and len(parts) == 2:
            context = await service.assemble_context(principal, parts[1])
            return context.as_tool_result()

        if action in {"close", "done"} and len(parts) == 2:
            goal = await service.close_goal(
                principal,
                parts[1],
                surface=surface,
            )
            return f"Closed goal {goal.id}: {goal.title}"
    except (LookupError, PermissionError, ValueError, KeyError) as exc:
        return f"Goal management error: {exc}"

    return USAGE


def _resource_kind(value: str) -> ResourceKind:
    normalized = value.lower()
    if normalized not in RESOURCE_KINDS:
        raise ValueError(f"resource kind must be one of {', '.join(RESOURCE_KINDS)}")
    return cast(ResourceKind, normalized)


__all__ = ["USAGE", "execute_goal_command"]
