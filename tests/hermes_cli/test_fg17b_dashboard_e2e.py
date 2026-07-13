"""Real-path E2E for the FG-17b dashboard backend against a throwaway Postgres.

Covers the security-critical surfaces of the new dashboard panels with real
imports, a real app store, and the real FastAPI endpoints (Starlette
``TestClient``) — no mocks of the data or policy layers:

* **Agent webview (highest-risk surface, C6/C8):** default-deny with no open
  session; in-scope reads run autonomously; off-scope / interactive-under-
  read-only / destructive actions escalate to a per-action approval that must
  be granted before anything runs; per-user session isolation (C2); every
  decision is traced (C8) under one session ``trace_id``.
* **Core-area view (FG-14 C7):** the read-only manifest projection surfaces the
  active Core globs + health.
* **GTS Centre (FG-18 C9):** the read-only graph is scoped to the resolved
  principal.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import asyncpg
import pytest

from hermes_cli.access import Principal, PrincipalStore
from hermes_cli.datastore import get_store, initialize_supabase_app
from hermes_cli.interactions import InteractionLedger

_IMAGE = (
    "postgres@sha256:"
    "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
)


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the FG-17b E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the FG-17b E2E test")

    subprocess.run(["docker", "pull", _IMAGE], check=True, capture_output=True)
    container = f"hermes-fg17b-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--env", "POSTGRES_PASSWORD=hermes-test",
            "--env", "POSTGRES_DB=hermes_test",
            "--publish", "127.0.0.1::5432",
            _IMAGE,
        ],
        check=True, capture_output=True, text=True,
    )
    try:
        port_result = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            check=True, capture_output=True, text=True,
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
            raise RuntimeError("Throwaway Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False, capture_output=True,
        )


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


async def _reset_and_enroll(dsn: str) -> None:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute(
            "DROP SCHEMA IF EXISTS app_dev CASCADE;"
            "DROP SCHEMA IF EXISTS app_prod CASCADE;"
        )
        await initialize_supabase_app(conn)
    finally:
        await conn.close()
    # Web + channels share the prod store (C3); enrol into it.
    principals = PrincipalStore(get_store("supabase-app", "prod", config=_config(dsn)))
    await principals.enroll("root", display="Root", role="owner")
    await principals.enroll("bob", display="Bob", role="member")


@pytest.fixture()
def client(postgres_dsn: str, tmp_path, monkeypatch):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    asyncio.run(_reset_and_enroll(postgres_dsn))

    from hermes_cli import web_server as ws

    prod = get_store("supabase-app", "prod", config=_config(postgres_dsn))
    monkeypatch.setattr(ws, "_comms_app_store", lambda: prod)
    monkeypatch.setattr(ws, "load_config", lambda: _config(postgres_dsn))
    # Fresh per-process webview registry so tests never share sessions.
    monkeypatch.setattr(ws, "_webview_registry", None)

    c = TestClient(ws.app)
    c.headers[ws._SESSION_HEADER_NAME] = ws._SESSION_TOKEN
    return c


# ---------------------------------------------------------------------------
# Agent webview — default-deny, consent scope, escalation, isolation, trace
# ---------------------------------------------------------------------------


def test_webview_default_deny_without_a_session(client) -> None:
    # No session opened: any action is refused (default-deny), not escalated.
    resp = client.post("/api/webview/action", json={"kind": "navigate", "url": "https://example.com"})
    assert resp.status_code == 403
    # And the caller has no session.
    got = client.get("/api/webview/session").json()
    assert got["configured"] is True and got["session"] is None


def test_webview_in_scope_read_allowed_off_scope_escalates_and_is_traced(
    client, postgres_dsn: str
) -> None:
    opened = client.post(
        "/api/webview/session",
        json={"allowed_domains": ["example.com"], "mode": "read_only"},
    ).json()
    trace_id = opened["session"]["trace_id"]

    # In-scope navigation is allowed autonomously (execution degrades gracefully
    # with no CDP browser attached, but the *decision* is allow).
    allow = client.post(
        "/api/webview/action",
        json={"kind": "navigate", "url": "https://example.com/docs"},
    ).json()
    assert allow["decision"] == "allow"

    # Off-scope navigation escalates to a queued per-action approval.
    esc = client.post(
        "/api/webview/action",
        json={"kind": "navigate", "url": "https://evil.test/phish"},
    ).json()
    assert esc["decision"] == "escalate"
    approval_id = esc["approval"]["id"]

    # The pending approval is visible on the session.
    sess = client.get("/api/webview/session").json()["session"]
    assert [p["id"] for p in sess["pending"]] == [approval_id]

    # C8: both the allow and the escalate were traced under the session trace.
    prod = get_store("supabase-app", "prod", config=_config(postgres_dsn))
    events, _ = asyncio.run(
        InteractionLedger(prod, config={}).get_trace(
            trace_id, Principal(user_id="root", display="Root", role="owner")
        )
    )
    platforms = {e.platform for e in events}
    refs = {e.ref for e in events}
    assert platforms == {"webview"}
    assert "webview:navigate" in refs  # the allowed + escalated navigations


def test_webview_destructive_escalates_then_runs_only_after_approval(client) -> None:
    client.post(
        "/api/webview/session",
        json={"allowed_domains": ["example.com"], "mode": "interactive"},
    )
    # A destructive action escalates even with a full interactive grant in-scope.
    esc = client.post(
        "/api/webview/action",
        json={"kind": "submit", "url": "https://example.com/pay"},
    ).json()
    assert esc["decision"] == "escalate"
    approval_id = esc["approval"]["id"]

    # Denying the approval discards it (the action never runs).
    denied = client.post(f"/api/webview/approval/{approval_id}", json={"grant": False}).json()
    assert denied["granted"] is False and denied["decision"] == "deny"
    assert client.get("/api/webview/session").json()["session"]["pending"] == []

    # A fresh escalation, then granting it, dispatches through the CDP handoff.
    esc2 = client.post(
        "/api/webview/action",
        json={"kind": "submit", "url": "https://example.com/pay"},
    ).json()
    granted = client.post(
        f"/api/webview/approval/{esc2['approval']['id']}", json={"grant": True}
    ).json()
    assert granted["granted"] is True and granted["decision"] == "allow"


def test_webview_sessions_are_isolated_per_user(client) -> None:
    # The owner-operator opens a session (attributed to the owner principal).
    client.post("/api/webview/session", json={"allowed_domains": ["example.com"]})
    assert client.get("/api/webview/session").json()["session"] is not None

    # Narrowing the read to another enrolled principal shows NO session — one
    # user can never see another's webview session (C2 isolation).
    other = client.get("/api/webview/session", params={"as": "bob"}).json()
    assert other["principal"] == "bob"
    assert other["session"] is None


def test_webview_open_rejects_bad_mode(client) -> None:
    resp = client.post("/api/webview/session", json={"mode": "root", "allowed_domains": []})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Core-area view (FG-14 C7) + GTS Centre (FG-18 C9)
# ---------------------------------------------------------------------------


def test_core_manifest_projection_is_readonly_and_healthy(client) -> None:
    data = client.get("/api/core/manifest").json()
    assert data["glob_count"] > 0
    assert isinstance(data["globs"], list) and data["globs"]
    # The boundary protects its own definition.
    assert data["self_protected"] is True
    assert "denials" in data


def test_gts_graph_is_scoped_to_the_resolved_principal(client) -> None:
    data = client.get("/api/gts/graph").json()
    assert data["configured"] is True
    assert data["principal"] == "root"
    for key in ("goals", "tasks", "skills", "task_goals", "task_skills"):
        assert isinstance(data[key], list)
    # FG-19 (merged): per-user assignment is live and each assignable node
    # carries its assignee + per-item grants (scoped by item_grants RLS).
    assert data["assignment"] == {"enabled": True, "scheme": "per-user"}
    for node in (*data["goals"], *data["tasks"]):
        assert "assignee_user_id" in node
        assert isinstance(node["grants"], list)
