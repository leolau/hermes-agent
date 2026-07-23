"""Tests for member management (PR-3): GoTrue admin client + MemberService.

* :class:`GoTrueAdminClient` — httpx-mocked: request shape (url, service-role
  headers, body), error mapping (409/422 → conflict, 401/403 → service-role
  error, network → MemberError), and list pagination.
* :class:`MemberService` — the owner/admin authorization gate, the "never
  create/assign owner" guard, GoTrue-account rollback when enrolment fails, and
  the principal ↔ account join in ``list_members`` — over a fake principal
  store + a fake admin client (the DB path itself is covered real against
  Postgres in ``test_access_e2e.py``).

All HTTP is mocked; no secret or password is asserted into logs.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from hermes_cli.access import Principal, Role
from hermes_cli.members import (
    GoTrueAdminClient,
    MemberAuthorizationError,
    MemberConflictError,
    MemberError,
    MemberService,
    MemberView,
    require_member_admin,
)

_URL = "https://supabase.example.com"
_KEY = "service-role-key-abcdefghijklmnopqrstuvwxyz0123456789"
_SUB = "a1b2c3d4-0000-4000-8000-000000000042"


def _mock_response(status_code: int, body: Any, *, ctype: str = "application/json"):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if isinstance(body, (dict, list)):
        resp.text = json.dumps(body)
        resp.json = MagicMock(return_value=body)
    else:
        resp.text = str(body)
        resp.json = MagicMock(side_effect=ValueError("not json"))
    resp.headers = {"content-type": ctype}
    return resp


def _client() -> GoTrueAdminClient:
    return GoTrueAdminClient(url=_URL, service_role_key=_KEY)


# ---------------------------------------------------------------------------
# GoTrueAdminClient — construction
# ---------------------------------------------------------------------------


def test_admin_client_requires_url_and_key() -> None:
    with pytest.raises(ValueError):
        GoTrueAdminClient(url="", service_role_key=_KEY)
    with pytest.raises(ValueError):
        GoTrueAdminClient(url=_URL, service_role_key="")


def test_admin_client_rejects_cleartext_non_loopback() -> None:
    with pytest.raises(ValueError):
        GoTrueAdminClient(url="http://supabase.example.com", service_role_key=_KEY)
    # loopback http is allowed (same-box Kong / dev)
    GoTrueAdminClient(url="http://127.0.0.1:8000", service_role_key=_KEY)


def test_admin_client_normalises_trailing_slash() -> None:
    client = GoTrueAdminClient(url=_URL + "/", service_role_key=_KEY)
    with patch("hermes_cli.members.httpx.request") as req:
        req.return_value = _mock_response(200, {"users": []})
        client.list_users()
    called_url = req.call_args.args[1]
    assert called_url.startswith(_URL + "/auth/v1/admin/users")
    assert "//auth" not in called_url


# ---------------------------------------------------------------------------
# GoTrueAdminClient — create_user
# ---------------------------------------------------------------------------


def test_create_user_request_shape_and_service_role_headers() -> None:
    client = _client()
    with patch("hermes_cli.members.httpx.request") as req:
        req.return_value = _mock_response(200, {"id": _SUB, "email": "m@x.io"})
        user = client.create_user(
            email="m@x.io", password="temp-pass-123", display="Mia"
        )
    assert user["id"] == _SUB
    method, url = req.call_args.args
    assert method == "POST"
    assert url == f"{_URL}/auth/v1/admin/users"
    headers = req.call_args.kwargs["headers"]
    assert headers["apikey"] == _KEY
    assert headers["Authorization"] == f"Bearer {_KEY}"
    body = req.call_args.kwargs["json"]
    assert body["email"] == "m@x.io"
    assert body["password"] == "temp-pass-123"
    assert body["email_confirm"] is True
    assert body["user_metadata"] == {"display_name": "Mia"}


def test_create_user_duplicate_maps_to_conflict() -> None:
    client = _client()
    for status in (409, 422):
        with patch("hermes_cli.members.httpx.request") as req:
            req.return_value = _mock_response(status, {"msg": "already registered"})
            with pytest.raises(MemberConflictError):
                client.create_user(email="dupe@x.io", password="p")


def test_create_user_missing_id_is_member_error() -> None:
    client = _client()
    with patch("hermes_cli.members.httpx.request") as req:
        req.return_value = _mock_response(200, {"email": "m@x.io"})
        with pytest.raises(MemberError):
            client.create_user(email="m@x.io", password="p")


def test_create_user_network_error_is_member_error() -> None:
    client = _client()
    with patch("hermes_cli.members.httpx.request") as req:
        req.side_effect = httpx.ConnectError("boom")
        with pytest.raises(MemberError):
            client.create_user(email="m@x.io", password="p")


def test_service_role_rejected_maps_to_member_error() -> None:
    client = _client()
    for status in (401, 403):
        with patch("hermes_cli.members.httpx.request") as req:
            req.return_value = _mock_response(status, {"msg": "no"})
            with pytest.raises(MemberError):
                client.create_user(email="m@x.io", password="p")


# ---------------------------------------------------------------------------
# GoTrueAdminClient — list / password / ban / delete
# ---------------------------------------------------------------------------


def test_list_users_paginates_until_short_page() -> None:
    client = _client()
    page1 = {"users": [{"id": f"u{i}", "email": f"{i}@x.io"} for i in range(1000)]}
    page2 = {"users": [{"id": "last", "email": "last@x.io"}]}
    with patch("hermes_cli.members.httpx.request") as req:
        req.side_effect = [
            _mock_response(200, page1),
            _mock_response(200, page2),
        ]
        users = client.list_users(per_page=1000)
    assert len(users) == 1001
    assert users["last"]["email"] == "last@x.io"
    assert req.call_count == 2


def test_set_password_puts_password() -> None:
    client = _client()
    with patch("hermes_cli.members.httpx.request") as req:
        req.return_value = _mock_response(200, {"id": _SUB})
        client.set_password(user_id=_SUB, password="new-temp-pass")
    method, url = req.call_args.args
    assert method == "PUT"
    assert url == f"{_URL}/auth/v1/admin/users/{_SUB}"
    assert req.call_args.kwargs["json"] == {"password": "new-temp-pass"}


def test_set_banned_uses_long_duration_and_none_to_clear() -> None:
    client = _client()
    with patch("hermes_cli.members.httpx.request") as req:
        req.return_value = _mock_response(200, {"id": _SUB})
        client.set_banned(user_id=_SUB, banned=True)
        banned_body = req.call_args.kwargs["json"]
        client.set_banned(user_id=_SUB, banned=False)
        unban_body = req.call_args.kwargs["json"]
    assert banned_body["ban_duration"] not in ("", "none")
    assert unban_body["ban_duration"] == "none"


def test_delete_user_tolerates_404() -> None:
    client = _client()
    with patch("hermes_cli.members.httpx.request") as req:
        req.return_value = _mock_response(404, "gone")
        client.delete_user(user_id=_SUB)  # no raise
    with patch("hermes_cli.members.httpx.request") as req:
        req.return_value = _mock_response(500, "err")
        with pytest.raises(MemberError):
            client.delete_user(user_id=_SUB)


# ---------------------------------------------------------------------------
# Authorization gate
# ---------------------------------------------------------------------------


def _principal(role: Role, user_id: str = "u1") -> Principal:
    return Principal(user_id=user_id, display="", role=role)


def test_require_member_admin_allows_owner_and_admin() -> None:
    require_member_admin(_principal("owner"))
    require_member_admin(_principal("admin"))


def test_require_member_admin_rejects_member_and_viewer() -> None:
    for role in ("member", "viewer"):
        with pytest.raises(MemberAuthorizationError):
            require_member_admin(_principal(role))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# MemberService — over fakes
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimal PrincipalStore stand-in for MemberService orchestration tests."""

    def __init__(self) -> None:
        self.principals: dict[str, Principal] = {}
        self.enroll_error: Exception | None = None

    async def enroll(
        self, user_id: str, *, display: str = "", role: Role = "member"
    ) -> Principal:
        if self.enroll_error is not None:
            raise self.enroll_error
        p = Principal(user_id=user_id, display=display, role=role)
        self.principals[user_id] = p
        return p

    async def list_principals(self) -> list[Principal]:
        return list(self.principals.values())

    async def set_role(self, user_id: str, role: Role) -> Principal:
        if user_id not in self.principals:
            raise KeyError(f"No such principal: {user_id}")
        existing = self.principals[user_id]
        if existing.role == "owner":
            raise ValueError("Cannot change the owner's role here")
        updated = Principal(user_id=user_id, display=existing.display, role=role)
        self.principals[user_id] = updated
        return updated


class _FakeAdmin:
    def __init__(self, *, users: dict[str, dict[str, Any]] | None = None) -> None:
        self.users = users or {}
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.passwords: list[str] = []
        self.bans: list[tuple[str, bool]] = []

    def create_user(self, *, email: str, password: str, display: str = "") -> dict:
        uid = f"gotrue-{len(self.created)}"
        self.created.append({"email": email, "id": uid})
        self.users[uid] = {"id": uid, "email": email}
        return {"id": uid, "email": email}

    def list_users(self) -> dict[str, dict[str, Any]]:
        return self.users

    def set_password(self, *, user_id: str, password: str) -> None:
        self.passwords.append(user_id)

    def set_banned(self, *, user_id: str, banned: bool) -> None:
        self.bans.append((user_id, banned))

    def delete_user(self, *, user_id: str) -> None:
        self.deleted.append(user_id)


def _service(store: _FakeStore, admin: _FakeAdmin) -> MemberService:
    return MemberService(store, admin)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_member_creates_account_and_enrolls() -> None:
    store, admin = _FakeStore(), _FakeAdmin()
    svc = _service(store, admin)
    principal = await svc.create_member(
        _principal("owner"),
        email="new@x.io",
        password="temp-123",
        display="New",
        role="member",
    )
    assert principal.role == "member"
    assert admin.created and admin.created[0]["email"] == "new@x.io"
    assert principal.user_id in store.principals


@pytest.mark.asyncio
async def test_create_member_requires_admin() -> None:
    store, admin = _FakeStore(), _FakeAdmin()
    svc = _service(store, admin)
    with pytest.raises(MemberAuthorizationError):
        await svc.create_member(
            _principal("member"), email="x@x.io", password="p"
        )
    assert not admin.created  # never touched GoTrue


@pytest.mark.asyncio
async def test_create_member_rejects_owner_role() -> None:
    store, admin = _FakeStore(), _FakeAdmin()
    svc = _service(store, admin)
    with pytest.raises(MemberError):
        await svc.create_member(
            _principal("owner"),
            email="x@x.io",
            password="p",
            role="owner",  # type: ignore[arg-type]
        )
    assert not admin.created


@pytest.mark.asyncio
async def test_create_member_rolls_back_gotrue_on_enroll_failure() -> None:
    store, admin = _FakeStore(), _FakeAdmin()
    store.enroll_error = RuntimeError("db down")
    svc = _service(store, admin)
    with pytest.raises(RuntimeError):
        await svc.create_member(
            _principal("admin"), email="x@x.io", password="p"
        )
    # The freshly created GoTrue account is deleted so no orphan lingers.
    assert admin.deleted == [admin.created[0]["id"]]


@pytest.mark.asyncio
async def test_list_members_joins_account_state() -> None:
    store = _FakeStore()
    store.principals = {
        "leo_owner": Principal(user_id="leo_owner", display="Leo", role="owner"),
        _SUB: Principal(user_id=_SUB, display="Mia", role="member"),
    }
    admin = _FakeAdmin(
        users={
            _SUB: {"id": _SUB, "email": "mia@x.io", "banned_until": None},
        }
    )
    svc = _service(store, admin)
    members = await svc.list_members(_principal("owner"))
    by_id = {m.user_id: m for m in members}
    # Owner enrolled before Supabase → unknown to GoTrue → blank email, active.
    assert by_id["leo_owner"].email == ""
    assert by_id["leo_owner"].active is True
    assert by_id[_SUB].email == "mia@x.io"
    assert by_id[_SUB].active is True


@pytest.mark.asyncio
async def test_list_members_marks_banned_inactive() -> None:
    store = _FakeStore()
    store.principals = {_SUB: Principal(user_id=_SUB, display="Mia", role="member")}
    admin = _FakeAdmin(
        users={_SUB: {"id": _SUB, "email": "mia@x.io", "banned_until": "2999-01-01T00:00:00Z"}}
    )
    svc = _service(store, admin)
    members = await svc.list_members(_principal("admin"))
    assert members[0].active is False


@pytest.mark.asyncio
async def test_set_member_role_guards_and_maps_errors() -> None:
    store = _FakeStore()
    store.principals = {
        "leo_owner": Principal(user_id="leo_owner", display="Leo", role="owner"),
        "m1": Principal(user_id="m1", display="M", role="member"),
    }
    admin = _FakeAdmin()
    svc = _service(store, admin)

    updated = await svc.set_member_role(_principal("owner"), user_id="m1", role="admin")
    assert updated.role == "admin"

    # Cannot re-role the owner via this path.
    with pytest.raises(MemberError):
        await svc.set_member_role(_principal("owner"), user_id="leo_owner", role="admin")
    # Cannot assign owner.
    with pytest.raises(MemberError):
        await svc.set_member_role(_principal("owner"), user_id="m1", role="owner")  # type: ignore[arg-type]
    # Unknown principal.
    with pytest.raises(MemberError):
        await svc.set_member_role(_principal("owner"), user_id="ghost", role="member")


@pytest.mark.asyncio
async def test_set_member_password_and_active_require_admin() -> None:
    store, admin = _FakeStore(), _FakeAdmin()
    svc = _service(store, admin)
    with pytest.raises(MemberAuthorizationError):
        await svc.set_member_password(_principal("viewer"), user_id="m1", password="p")
    with pytest.raises(MemberAuthorizationError):
        await svc.set_member_active(_principal("member"), user_id="m1", active=False)
    assert not admin.passwords and not admin.bans


@pytest.mark.asyncio
async def test_set_member_active_bans_and_unbans() -> None:
    store, admin = _FakeStore(), _FakeAdmin()
    svc = _service(store, admin)
    await svc.set_member_active(_principal("owner"), user_id="m1", active=False)
    await svc.set_member_active(_principal("admin"), user_id="m1", active=True)
    assert admin.bans == [("m1", True), ("m1", False)]


def test_member_view_as_dict_flags_owner() -> None:
    view = MemberView(
        user_id="leo_owner",
        display="Leo",
        role="owner",
        email="",
        active=True,
        channels=("telegram:1",),
    )
    d = view.as_dict()
    assert d["is_owner"] is True
    assert d["channels"] == ["telegram:1"]
