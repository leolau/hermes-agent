"""Endpoint tests for the dashboard session-chat + create-session routes.

Real ``SessionDB`` (SQLite under a throwaway ``HERMES_HOME``) exercises the DB
boundary; the C1 principal resolver and the one-brain turn are stubbed so the
tests stay hermetic and focus on the endpoint's contract:

* ``POST /api/sessions`` creates an owner-attributed session row (and rejects
  unsafe ids / conflicts);
* ``POST /api/sessions/{id}/chat`` loads the persisted history and forwards it
  **verbatim** to the shared one-brain runner (no synthetic message, no
  ephemeral system prompt), returns the assistant reply + usage, and 404s an
  unknown session / 400s an empty message.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        import hermes_constants

        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    except (ImportError, AttributeError):
        pass
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db")
    return tmp_path


@pytest.fixture
def client(monkeypatch):
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    # Resolve to the enrolled owner without a Postgres principal store.
    owner = SimpleNamespace(user_id="root", display="Root Owner", role="owner", is_owner=True)

    async def _fake_principal(request, *, allow_as=False):
        return owner

    monkeypatch.setattr(web_server, "_comms_resolve_principal", _fake_principal)

    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


@pytest.fixture
def capture_turn(monkeypatch):
    """Stub the one-brain runner, capturing what the endpoint forwards."""
    import gateway.session_chat as session_chat

    captured: dict = {}

    def _fake_turn(*, session_db, user_message, conversation_history, session_id=None, **kwargs):
        captured["session_db"] = session_db
        captured["user_message"] = user_message
        captured["conversation_history"] = conversation_history
        captured["session_id"] = session_id
        captured["kwargs"] = kwargs
        return (
            {"final_response": f"echo:{user_message}", "session_id": session_id},
            {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        )

    monkeypatch.setattr(session_chat, "run_session_turn_sync", _fake_turn)
    return captured


def test_create_session_returns_id(client):
    resp = client.post("/api/sessions", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"].startswith("home_")
    assert data["source"] == "agent_home"


def test_create_session_rejects_unsafe_id(client):
    resp = client.post("/api/sessions", json={"id": "../etc/passwd"})
    assert resp.status_code == 400


def test_create_session_conflict(client):
    resp = client.post("/api/sessions", json={"id": "dup-1"})
    assert resp.status_code == 200
    again = client.post("/api/sessions", json={"id": "dup-1"})
    assert again.status_code == 409


def test_chat_empty_message_rejected(client, capture_turn):
    client.post("/api/sessions", json={"id": "s-empty"})
    resp = client.post("/api/sessions/s-empty/chat", json={"message": "   "})
    assert resp.status_code == 400
    assert "message" not in capture_turn  # runner never invoked


def test_chat_unknown_session_404(client, capture_turn):
    resp = client.post("/api/sessions/does-not-exist/chat", json={"message": "hi"})
    assert resp.status_code == 404
    assert "user_message" not in capture_turn


def test_chat_roundtrip_forwards_history_verbatim(client, capture_turn):
    import hermes_state

    # Seed a real alternating transcript in the throwaway SessionDB.
    db = hermes_state.SessionDB()
    try:
        db.ensure_session("s-hist", source="agent_home")
        db.append_message("s-hist", "user", "first")
        db.append_message("s-hist", "assistant", "second")
        expected = db.get_messages_as_conversation("s-hist")
    finally:
        db.close()

    resp = client.post("/api/sessions/s-hist/chat", json={"message": "third"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == {"role": "assistant", "content": "echo:third"}
    assert data["usage"] == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    assert data["session_id"] == "s-hist"

    # The endpoint forwarded the persisted history unchanged (cache/alternation
    # safety: no synthetic user turn appended, message passed separately).
    assert capture_turn["conversation_history"] == expected
    assert capture_turn["user_message"] == "third"
    assert capture_turn["session_id"] == "s-hist"
    # No ephemeral system prompt is smuggled through kwargs.
    assert "ephemeral_system_prompt" not in capture_turn["kwargs"]
