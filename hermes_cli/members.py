"""Member management (PR-3) — GoTrue admin create → principal enrolment.

The owner/admin surface for onboarding and managing additional members of the
shared Hermes brain. A "member" is a Supabase Auth (GoTrue) account whose
subject UUID is enrolled as a :class:`~hermes_cli.access.Principal`, so the same
identity that logs in (via the ``supabase`` dashboard-auth provider) is the one
the multi-user RLS layer scopes data to.

Two collaborators, kept apart on purpose:

* :class:`GoTrueAdminClient` owns the account system — it calls GoTrue's
  **admin** endpoints (``/auth/v1/admin/users``) with the *service-role* key to
  create accounts, set/reset passwords, and ban/unban (deactivate/reactivate).
  It never touches Hermes state and never logs a secret or a password.
* :class:`MemberService` orchestrates: it authorises the actor (owner/admin
  only), drives the GoTrue account operation, and mirrors it into the
  :class:`~hermes_cli.access.PrincipalStore` (enrol / set-role). Account
  creation is transactional-ish: if enrolment fails after the GoTrue user is
  created, the GoTrue user is rolled back so a half-created member can't linger.

**Why the service-role key is env-only.** It can mint and delete any account,
so it is a credential — it lives in ``~/.hermes/.env`` (or the process env),
never in ``config.yaml`` and never in a browser. Only server-side code
(this module, the CLI, the owner/admin-guarded API) ever holds it.

**Signup stays closed.** This module is the *only* way accounts are created;
open self-signup is disabled at the GoTrue server
(``GOTRUE_DISABLE_SIGNUP=true``). New members always come through an
owner/admin here, with a temporary password the owner hands over.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from hermes_cli.access import ROLES, Principal, PrincipalStore, Role

logger = logging.getLogger(__name__)

# httpx timeout for the GoTrue admin round trips.
_ADMIN_TIMEOUT_SEC = 15.0

# GoTrue's ban semantics: a long finite duration bans (blocks login); the
# literal "none" clears the ban. Reversible, unlike deleting the account.
_BAN_DURATION = "876000h"  # ~100 years
_UNBAN_DURATION = "none"

# Roles a member-management actor may assign. ``owner`` is deliberately absent:
# the single owner only changes via the approval-gated ``hermes owner transfer``.
ASSIGNABLE_ROLES: tuple[Role, ...] = ("admin", "member", "viewer")


class MemberError(RuntimeError):
    """A member operation failed (GoTrue rejected it, or state was wrong)."""


class MemberConflictError(MemberError):
    """The account already exists (duplicate email)."""


class MemberAuthorizationError(PermissionError):
    """The acting principal is not allowed to manage members."""


def require_member_admin(actor: Principal) -> None:
    """Authorise ``actor`` for member management — owner or admin only.

    Members and viewers may never create, re-role, reset, or deactivate other
    members. This is the single authorization gate every :class:`MemberService`
    mutation and the API/CLI surfaces share.
    """
    if actor.role not in ("owner", "admin"):
        raise MemberAuthorizationError(
            "Only the owner or an admin may manage members "
            f"(actor role: {actor.role})."
        )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberView:
    """A principal joined with its GoTrue account state, for list/inspect.

    ``email`` / ``active`` come from GoTrue (empty / ``True`` when the account
    is unknown to GoTrue — e.g. the bootstrap owner enrolled before Supabase,
    or a channel-only principal). ``active`` is ``False`` when the account is
    currently banned.
    """

    user_id: str
    display: str
    role: Role
    email: str
    active: bool
    channels: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "display": self.display,
            "role": self.role,
            "email": self.email,
            "active": self.active,
            "channels": list(self.channels),
            "is_owner": self.role == "owner",
        }


# ---------------------------------------------------------------------------
# GoTrue admin client
# ---------------------------------------------------------------------------


class GoTrueAdminClient:
    """Thin wrapper over GoTrue's admin user API (service-role authenticated).

    All calls carry the service-role key in both the ``apikey`` header and the
    ``Authorization: Bearer`` header (GoTrue requires the bearer to be a
    service-role JWT for ``/admin`` routes). Errors map to :class:`MemberError`
    (or :class:`MemberConflictError` for a duplicate email); no response body,
    key, or password is ever logged.
    """

    def __init__(
        self,
        *,
        url: str,
        service_role_key: str,
        timeout: float = _ADMIN_TIMEOUT_SEC,
    ) -> None:
        if not url:
            raise ValueError("url is required")
        if not service_role_key:
            raise ValueError("service_role_key is required")
        self._base = url.rstrip("/")
        self._require_https_or_loopback(self._base)
        self._key = service_role_key
        self._timeout = timeout

    # ---- account operations ------------------------------------------------

    def create_user(
        self,
        *,
        email: str,
        password: str,
        display: str = "",
        email_confirm: bool = True,
    ) -> dict[str, Any]:
        """Create a confirmed GoTrue account and return its user object.

        ``email_confirm=True`` marks the address confirmed so the member can
        sign in immediately with the temporary password (no email round trip).
        A duplicate email raises :class:`MemberConflictError`.
        """
        user_metadata: dict[str, str] = {}
        if display:
            user_metadata["display_name"] = display
        body: dict[str, Any] = {
            "email": email,
            "password": password,
            "email_confirm": email_confirm,
        }
        if user_metadata:
            body["user_metadata"] = user_metadata
        response = self._request("POST", "/auth/v1/admin/users", json=body)
        if response.status_code in (409, 422):
            raise MemberConflictError(
                f"An account already exists for {email!r}."
            )
        user = self._ok_json(response, "create user")
        if not isinstance(user.get("id"), str) or not user["id"]:
            raise MemberError("GoTrue create-user response missing 'id'.")
        return user

    def list_users(self, *, per_page: int = 1000) -> dict[str, dict[str, Any]]:
        """Return ``{user_id: user_object}`` for every GoTrue account.

        Paginates until GoTrue returns a short page. Used to join account state
        (email, banned) onto the enrolled principals.
        """
        users: dict[str, dict[str, Any]] = {}
        page = 1
        while True:
            query = urllib.parse.urlencode({"page": page, "per_page": per_page})
            response = self._request(
                "GET", f"/auth/v1/admin/users?{query}"
            )
            payload = self._ok_json(response, "list users")
            batch = payload.get("users")
            if not isinstance(batch, list) or not batch:
                break
            for user in batch:
                if isinstance(user, dict) and isinstance(user.get("id"), str):
                    users[user["id"]] = user
            if len(batch) < per_page:
                break
            page += 1
        return users

    def set_password(self, *, user_id: str, password: str) -> None:
        """Reset a member's password (owner-issued temporary password flow)."""
        response = self._request(
            "PUT",
            f"/auth/v1/admin/users/{urllib.parse.quote(user_id)}",
            json={"password": password},
        )
        self._ok_json(response, "set password")

    def set_banned(self, *, user_id: str, banned: bool) -> None:
        """Ban (deactivate) or unban (reactivate) a member's account.

        Banning blocks login without destroying the account, so history and
        any owned rows stay intact and the member can be reactivated.
        """
        duration = _BAN_DURATION if banned else _UNBAN_DURATION
        response = self._request(
            "PUT",
            f"/auth/v1/admin/users/{urllib.parse.quote(user_id)}",
            json={"ban_duration": duration},
        )
        self._ok_json(response, "set banned")

    def delete_user(self, *, user_id: str) -> None:
        """Delete a GoTrue account (used to roll back a failed enrolment)."""
        response = self._request(
            "DELETE",
            f"/auth/v1/admin/users/{urllib.parse.quote(user_id)}",
        )
        if response.status_code not in (200, 204, 404):
            raise MemberError(
                f"GoTrue delete-user failed ({response.status_code})."
            )

    # ---- internals ---------------------------------------------------------

    def _request(
        self, method: str, path: str, *, json: Any | None = None
    ) -> httpx.Response:
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            return httpx.request(
                method,
                f"{self._base}{path}",
                json=json,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise MemberError(
                f"Supabase admin endpoint unreachable: {exc}"
            ) from exc

    def _ok_json(self, response: httpx.Response, what: str) -> dict[str, Any]:
        if response.status_code == 401 or response.status_code == 403:
            raise MemberError(
                f"GoTrue rejected the service-role key on {what} "
                f"({response.status_code}); check the service_role_key."
            )
        if response.status_code not in (200, 201):
            raise MemberError(
                f"GoTrue {what} failed ({response.status_code})."
            )
        ctype = response.headers.get("content-type", "")
        if "application/json" not in ctype:
            return {}
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _require_https_or_loopback(url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == "https":
            return
        if parsed.scheme == "http" and (parsed.hostname or "") in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            return
        raise ValueError(
            f"Supabase url must be https:// (or http on localhost), got {url!r}"
        )


# ---------------------------------------------------------------------------
# Member service (authorises + orchestrates GoTrue ↔ PrincipalStore)
# ---------------------------------------------------------------------------


class MemberService:
    """Owner/admin member management over GoTrue + the principal store.

    Every mutation runs :func:`require_member_admin` on the acting principal
    first, then performs the GoTrue account operation and mirrors it into the
    :class:`PrincipalStore`. The service never creates or transfers the owner —
    that stays with ``hermes owner``.
    """

    def __init__(self, store: PrincipalStore, admin: GoTrueAdminClient) -> None:
        self._store = store
        self._admin = admin

    async def create_member(
        self,
        actor: Principal,
        *,
        email: str,
        password: str,
        display: str = "",
        role: Role = "member",
    ) -> Principal:
        """Create a GoTrue account and enrol its subject as a principal.

        Rolls back the GoTrue account if enrolment fails, so a half-created
        member never lingers. ``role`` must be assignable (never ``owner``).
        """
        require_member_admin(actor)
        email = (email or "").strip()
        if not email:
            raise MemberError("email is required")
        if not password:
            raise MemberError("password is required")
        if role not in ASSIGNABLE_ROLES:
            raise MemberError(
                f"role must be one of {ASSIGNABLE_ROLES}; got {role!r}. "
                "(The owner is set via 'hermes owner'.)"
            )

        user = self._admin.create_user(
            email=email, password=password, display=display
        )
        user_id = str(user["id"])
        try:
            principal = await self._store.enroll(
                user_id, display=display or email, role=role
            )
        except Exception:
            # Roll back the freshly created account so a failed enrolment
            # doesn't leave an orphan GoTrue user that can log in with no
            # principal (which the resolver would 409 on).
            try:
                self._admin.delete_user(user_id=user_id)
            except MemberError:
                logger.warning(
                    "member create: enrolment failed AND GoTrue rollback "
                    "failed for a new account; a manual cleanup may be needed."
                )
            raise
        return principal

    async def list_members(self, actor: Principal) -> list[MemberView]:
        """List every principal joined with its GoTrue account state."""
        require_member_admin(actor)
        principals = await self._store.list_principals()
        try:
            accounts = self._admin.list_users()
        except MemberError:
            # GoTrue may be briefly unreachable; still return the principals
            # (management is principal-first) with unknown account state.
            logger.warning(
                "member list: GoTrue account state unavailable; listing "
                "principals without email/active."
            )
            accounts = {}
        views: list[MemberView] = []
        for principal in principals:
            account = accounts.get(principal.user_id)
            email = ""
            active = True
            if account is not None:
                email = str(account.get("email", "") or "")
                active = not _is_banned(account)
            views.append(
                MemberView(
                    user_id=principal.user_id,
                    display=principal.display,
                    role=principal.role,
                    email=email,
                    active=active,
                    channels=principal.channels,
                )
            )
        return views

    async def set_member_role(
        self, actor: Principal, *, user_id: str, role: Role
    ) -> Principal:
        """Change a member's role (never the owner's; never *to* owner)."""
        require_member_admin(actor)
        if role not in ASSIGNABLE_ROLES:
            raise MemberError(
                f"role must be one of {ASSIGNABLE_ROLES}; got {role!r}."
            )
        try:
            return await self._store.set_role(user_id, role)
        except KeyError as exc:
            raise MemberError(str(exc)) from exc
        except ValueError as exc:
            raise MemberError(str(exc)) from exc

    async def set_member_password(
        self, actor: Principal, *, user_id: str, password: str
    ) -> None:
        """Reset a member's password to a new owner-issued temporary one."""
        require_member_admin(actor)
        if not password:
            raise MemberError("password is required")
        self._admin.set_password(user_id=user_id, password=password)

    async def set_member_active(
        self, actor: Principal, *, user_id: str, active: bool
    ) -> None:
        """Deactivate (ban) or reactivate (unban) a member's login."""
        require_member_admin(actor)
        self._admin.set_banned(user_id=user_id, banned=not active)


def _is_banned(account: dict[str, Any]) -> bool:
    """Whether a GoTrue user object is currently banned.

    GoTrue exposes ``banned_until`` (an RFC3339 timestamp) on the admin user
    object; a non-empty, non-"none" value means the account is banned. We treat
    any present value conservatively as banned rather than parsing the instant,
    because deactivation always writes a far-future ban.
    """
    value = account.get("banned_until")
    if not isinstance(value, str):
        return False
    value = value.strip().lower()
    return bool(value) and value not in ("none", "null")


# ---------------------------------------------------------------------------
# Config resolution — service-role key is env-only (a credential)
# ---------------------------------------------------------------------------


def _load_supabase_auth_section() -> dict[str, Any]:
    """Return ``dashboard.supabase_auth`` from config.yaml, or ``{}``.

    Reused for the GoTrue base ``url`` — the same non-secret surface the
    ``supabase`` dashboard-auth provider reads.
    """
    try:
        from hermes_cli.config import cfg_get, load_config

        cfg = load_config()
    except Exception as exc:  # noqa: BLE001 — broad catch is intentional
        logger.debug(
            "members: load_config() raised %s; env-only configuration", exc
        )
        return {}
    section = cfg_get(cfg, "dashboard", "supabase_auth", default=None)
    return section if isinstance(section, dict) else {}


def _resolve_url(section: dict[str, Any]) -> str:
    for env_name in ("HERMES_DASHBOARD_SUPABASE_URL", "SUPABASE_URL"):
        env = os.environ.get(env_name, "").strip()
        if env:
            return env
    return str(section.get("url", "") or "").strip()


def _resolve_service_role_key() -> str:
    """The service-role key — env only (never config.yaml / never a browser)."""
    for env_name in (
        "HERMES_DASHBOARD_SUPABASE_SERVICE_ROLE_KEY",
        "HERMES_SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_KEY",
    ):
        env = os.environ.get(env_name, "").strip()
        if env:
            return env
    return ""


def load_admin_client() -> Optional[GoTrueAdminClient]:
    """Build a :class:`GoTrueAdminClient` from config/env, or ``None``.

    Returns ``None`` (rather than raising) when the GoTrue ``url`` or the
    service-role key is absent, so callers can surface a clean "member
    management isn't configured" state instead of a stack trace.
    """
    section = _load_supabase_auth_section()
    url = _resolve_url(section)
    key = _resolve_service_role_key()
    if not url or not key:
        return None
    try:
        return GoTrueAdminClient(url=url, service_role_key=key)
    except ValueError as exc:
        logger.warning("members: admin client construction failed: %s", exc)
        return None


ADMIN_UNCONFIGURED_MESSAGE = (
    "Member management is not configured. Set the Supabase GoTrue url "
    "(dashboard.supabase_auth.url or HERMES_DASHBOARD_SUPABASE_URL / "
    "SUPABASE_URL) and the service-role key in the environment "
    "(HERMES_DASHBOARD_SUPABASE_SERVICE_ROLE_KEY / SUPABASE_SERVICE_ROLE_KEY — "
    "a credential, never config.yaml)."
)
