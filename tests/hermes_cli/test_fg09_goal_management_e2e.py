"""Real-path FG-09 goal integration across web, channels, Telegram, and MCP."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import asyncpg
import pytest

from gateway.platforms.base import MessageEvent
from gateway.session import Platform, SessionSource
from gateway.slash_commands import GatewaySlashCommandsMixin
from hermes_cli.access import Principal, PrincipalStore
from hermes_cli.datastore import get_store, initialize_supabase_app
from hermes_cli.goal_management import GoalManagementService
from hermes_cli.goals import GoalMetric
from hermes_cli.task_registry import TaskSpec

_PGVECTOR_IMAGE = (
    "pgvector/pgvector@sha256:"
    "1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the FG-09 E2E test")
    daemon = subprocess.run(
        ["docker", "info"],
        check=False,
        capture_output=True,
        text=True,
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the FG-09 E2E test")

    subprocess.run(
        ["docker", "pull", _PGVECTOR_IMAGE],
        check=True,
        capture_output=True,
    )
    container = f"hermes-fg09-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--env",
            "POSTGRES_PASSWORD=hermes-test",
            "--env",
            "POSTGRES_DB=hermes_test",
            "--publish",
            "127.0.0.1::5432",
            _PGVECTOR_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        port_result = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            check=True,
            capture_output=True,
            text=True,
        )
        port = port_result.stdout.strip().rsplit(":", 1)[1]
        dsn = f"postgresql://postgres:hermes-test@127.0.0.1:{port}/hermes_test"
        for _ in range(60):
            try:
                asyncio.run(_probe_postgres(dsn))
                break
            except (OSError, asyncpg.PostgresError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Throwaway pgvector Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False,
            capture_output=True,
        )


def _config(dsn: str) -> dict[str, object]:
    return {
        "datastore": {
            "mode": "dev",
            "supabase_app": {"dsn": dsn},
        }
    }


class _FakeTool:
    def __init__(self, function):
        self.name = function.__name__
        self.fn = function


class _FakeToolManager:
    def __init__(self) -> None:
        self._tools: dict[str, _FakeTool] = {}

    def add_tool(self, function) -> None:
        self._tools[function.__name__] = _FakeTool(function)

    def list_tools(self) -> list[_FakeTool]:
        return list(self._tools.values())


class _FakeFastMCP:
    def __init__(self, name: str, *, instructions: str) -> None:
        self.name = name
        self.instructions = instructions
        self._tool_manager = _FakeToolManager()

    def tool(self):
        def decorator(function):
            self._tool_manager.add_tool(function)
            return function

        return decorator


async def _reset(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    try:
        await connection.execute(
            "DROP SCHEMA IF EXISTS app_dev CASCADE;"
            "DROP SCHEMA IF EXISTS app_prod CASCADE;"
        )
        await initialize_supabase_app(connection)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_cross_surface_goal_management_scope_and_cache_safety(
    postgres_dsn: str,
    tmp_path,
    monkeypatch,
) -> None:
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("starlette is required for the FG-09 E2E test")

    await _reset(postgres_dsn)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    config = _config(postgres_dsn)
    prod = get_store("supabase-app", "prod", config=config)
    service = GoalManagementService(prod, audit_store=prod)
    await service.initialize()

    principals = PrincipalStore(prod)
    root = await principals.enroll("root", display="Root", role="owner")
    alice = await principals.enroll("alice", display="Alice", role="member")
    bob = await principals.enroll("bob", display="Bob", role="member")
    await principals.link_channel(root.user_id, "telegram", "tg-root")
    await principals.link_channel(root.user_id, "slack", "slack-root")
    await principals.link_channel(root.user_id, "mcp", "tok-root")
    await principals.link_channel(alice.user_id, "mcp", "tok-alice")

    memory = await service.memory.write(root, "Release acceptance evidence")
    task = await service.tasks.create_task(
        root,
        TaskSpec(
            title="Publish acceptance evidence",
            description="",
            trigger_state="pending",
            completion_state="completed",
            progress_states=("pending", "in_progress", "completed"),
        ),
    )
    tool = await service.tools.create(
        root,
        "release-dashboard",
        "in_house",
        status="enabled",
    )

    import hermes_cli.config as config_module
    import hermes_cli.web_server as web_server

    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(web_server, "_comms_app_store", lambda: prod)
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN

    created = client.post(
        "/api/comms/goals",
        json={"title": "Ship FG-09", "priority": "high"},
    )
    assert created.status_code == 200
    goal_id = created.json()["goal"]["id"]
    for resource_kind, resource_ref in (
        ("memory", memory.id),
        ("task", task.id),
        ("tool", tool.name),
    ):
        linked = client.post(
            f"/api/comms/goals/{goal_id}/links",
            json={
                "resource_kind": resource_kind,
                "resource_ref": resource_ref,
            },
        )
        assert linked.status_code == 200

    telegram = MessageEvent(
        text=f"/goals advance {goal_id} task {task.id} in_progress",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="chat-root",
            user_id="tg-root",
            internal_user_id=root.user_id,
        ),
    )
    telegram_result = await GatewaySlashCommandsMixin()._handle_goals_command(
        telegram
    )
    assert "in_progress" in telegram_result

    import mcp_serve

    monkeypatch.setattr(mcp_serve, "_MCP_SERVER_AVAILABLE", True)
    monkeypatch.setattr(mcp_serve, "FastMCP", _FakeFastMCP)
    monkeypatch.setattr(mcp_serve, "_get_app_store", lambda mode=None: prod)
    monkeypatch.setattr(
        mcp_serve,
        "_get_principal_store",
        lambda: PrincipalStore(prod),
    )
    monkeypatch.setenv(mcp_serve.MCP_PRINCIPAL_ENV, "tok-root")
    mcp = mcp_serve.create_mcp_server()
    tool_names_before = {
        registered.name for registered in mcp._tool_manager.list_tools()
    }
    instructions_before = mcp.instructions
    goals_manage = mcp._tool_manager._tools["goals_manage"].fn
    mcp_list = json.loads(await goals_manage(action="list"))
    assert goal_id in {goal["id"] for goal in mcp_list["goals"]}
    goal_context = mcp._tool_manager._tools["goal_context"].fn
    first_context_json = await goal_context(goal_id=goal_id)
    first_context = json.loads(first_context_json)
    assert first_context["goal"]["status"] == "active"
    assert {item["link"]["resource_kind"] for item in first_context["resources"]} == {
        "memory",
        "task",
        "tool",
    }
    first_task = next(
        item["resource"]
        for item in first_context["resources"]
        if item["link"]["resource_kind"] == "task"
    )
    assert first_task["current_state"] == "in_progress"

    channel = MessageEvent(
        text=f"/goals advance {goal_id} task {task.id} completed",
        source=SessionSource(
            platform=Platform.SLACK,
            chat_id="channel-root",
            user_id="slack-root",
            internal_user_id=root.user_id,
        ),
    )
    channel_result = await GatewaySlashCommandsMixin()._handle_goals_command(channel)
    assert "completed" in channel_result

    second_context = json.loads(await goal_context(goal_id=goal_id))
    assert second_context["goal"]["status"] == "done"
    assert any(
        item["note"] == f"channel advanced linked task {task.id} to completed"
        for item in second_context["progress"]
    )
    web_context = client.get(f"/api/comms/goals/{goal_id}/context")
    assert web_context.status_code == 200
    assert web_context.json()["context"] == second_context

    assert json.loads(first_context_json) == first_context
    assert mcp.instructions == instructions_before
    assert {
        registered.name for registered in mcp._tool_manager.list_tools()
    } == tool_names_before

    bob_memory = await service.memory.write(bob, "Bob private evidence")
    with pytest.raises(PermissionError, match="disjoint private scopes"):
        await service.link(
            root,
            goal_id,
            "memory",
            bob_memory.id,
            surface="web",
        )
    bob_goal = await service.create_goal(
        bob,
        "Bob private goal",
        surface="web",
    )
    await service.link(
        bob,
        bob_goal.id,
        "memory",
        bob_memory.id,
        surface="web",
    )
    assert all(
        goal.id != bob_goal.id for goal in await service.list_goals(alice)
    )
    with pytest.raises(LookupError):
        await service.assemble_context(alice, bob_goal.id)
    await service.prioritise(
        root,
        bob_goal.id,
        "urgent",
        surface="web",
    )
    owner_context = await service.assemble_context(root, bob_goal.id)
    assert owner_context.resources[0].link.resource_ref == bob_memory.id

    channel_store = get_store(
        "supabase-app",
        "dev",
        source=channel.source,
        config=config,
    )
    assert channel_store.mode == "prod"


@pytest.mark.asyncio
async def test_metric_completion_and_tool_retirement_approval(
    postgres_dsn: str,
    tmp_path,
    monkeypatch,
) -> None:
    await _reset(postgres_dsn)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    prod = get_store("supabase-app", "prod", config=_config(postgres_dsn))
    owner = Principal(user_id="root", display="Root", role="owner")
    service = GoalManagementService(prod, audit_store=prod)
    await service.initialize()

    shared_goal = await service.create_goal(
        owner,
        "Shared but owner-managed",
        visibility="shared",
        surface="web",
    )
    member = Principal(user_id="alice", display="Alice", role="member")
    assert shared_goal.id in {
        goal.id for goal in await service.list_goals(member)
    }
    with pytest.raises(PermissionError, match="may not mutate goal"):
        await service.prioritise(
            member,
            shared_goal.id,
            "high",
            surface="mcp",
        )

    metric_goal = await service.create_goal(
        owner,
        "Complete measurable work",
        surface="web",
    )
    await service.goals.add_metric(
        owner,
        metric_goal.id,
        GoalMetric(name="items", target=2, current=0),
    )
    metric = await service.advance_metric(
        owner,
        metric_goal.id,
        "items",
        2,
        surface="mcp",
    )
    assert metric.achieved is True
    completed_goal = await service.goals.get_goal(owner, metric_goal.id)
    assert completed_goal is not None
    assert completed_goal.status == "done"

    dependent_goal = await service.create_goal(
        owner,
        "Use temporary tool",
        surface="web",
    )
    tool = await service.tools.create(
        owner,
        "temporary-goal-tool",
        "in_house",
        status="enabled",
    )
    await service.link(
        owner,
        dependent_goal.id,
        "tool",
        tool.name,
        surface="web",
    )

    denied = GoalManagementService(
        prod,
        audit_store=prod,
        approval_callback=lambda *_args, **_kwargs: "deny",
    )
    with pytest.raises(PermissionError, match="approval denied"):
        await denied.retire_tool(owner, tool.name, surface="web")
    assert await service.tools.get_for_principal(owner, tool.name) is not None

    approved = GoalManagementService(
        prod,
        audit_store=prod,
        approval_callback=lambda *_args, **_kwargs: "once",
    )
    assert await approved.retire_tool(owner, tool.name, surface="web") == 1
    assert await service.tools.get_for_principal(owner, tool.name) is None
    paused_goal = await service.goals.get_goal(owner, dependent_goal.id)
    assert paused_goal is not None
    assert paused_goal.status == "paused"
    progress = await service.goals.list_progress(owner, dependent_goal.id)
    assert progress[-1]["note"] == f"Linked tool retired: {tool.name}"
