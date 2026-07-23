"""SupabaseAuthProvider — email/password dashboard auth via Supabase (GoTrue).

A password :class:`~hermes_cli.dashboard_auth.DashboardAuthProvider` that
authenticates against a (self-hosted) Supabase Auth / GoTrue server. It plugs
into the same framework as the ``basic`` and ``self-hosted`` providers, but
instead of minting its own stateless sessions (``basic``) or doing an OIDC
redirect (``self-hosted``), it:

  1. exchanges an email + password at GoTrue's **password grant**
     (``POST {url}/auth/v1/token?grant_type=password``),
  2. **verifies the returned access-token JWT locally** (HS256 against GoTrue's
     shared ``jwt_secret``) and maps its claims onto a
     :class:`~hermes_cli.dashboard_auth.Session`,
  3. refreshes via the **refresh grant**
     (``POST {url}/auth/v1/token?grant_type=refresh_token``).

The verified ``sub`` claim is the Supabase user's UUID — the same identifier
the multi-user principal layer uses as ``principal.user_id`` (see
``hermes_cli/access.py``), so "who logged in" and "who Hermes scopes data to"
are one identity. That is the whole point of using Supabase here: it is the
member-account system behind per-principal isolation.

Why verify the *access* token (not an ID token)? GoTrue does not issue an OIDC
ID token on the password grant; its access token IS a signed JWT carrying the
identity claims (``sub``, ``email``, ``role``, ``user_metadata``), signed with
the server's ``jwt_secret``. Verifying it locally means the per-request
``verify_session`` never has to call GoTrue.

**Signing algorithm.** This provider verifies HS256 (GoTrue's default —
symmetric, using the shared ``jwt_secret``). Projects that have migrated to
asymmetric JWT signing keys (RS256/ES256 via a JWKS endpoint) are not supported
here yet; configure the shared ``jwt_secret`` (the self-hosted default).

**Signup is never performed.** This provider only logs *existing* users in.
Member accounts are created by an admin (PR-3's ``hermes member`` flow, which
uses the GoTrue admin API server-side). Disable open signup at the GoTrue
server itself (``GOTRUE_DISABLE_SIGNUP=true``) so the ``/signup`` endpoint
can't be used to self-register — this provider has no ``/signup`` path and the
dashboard exposes none.

Configuration surfaces (env wins over config.yaml when set non-empty, matching
the ``basic`` / ``self-hosted`` precedence convention)::

    # config.yaml — canonical surface
    dashboard:
      supabase_auth:
        url: "https://supabase.example.com"   # required; GoTrue base (Kong)
        anon_key: "<supabase anon key>"        # required; sent as the apikey header
        # jwt_secret verifies the access token — it is a CREDENTIAL, so prefer
        # the env var / ~/.hermes/.env over config.yaml.
        jwt_secret: "<GoTrue GOTRUE_JWT_SECRET>"
        audience: "authenticated"              # optional; GoTrue default

    # Environment overrides (Docker / secret injection). The bare SUPABASE_*
    # names are recognised as fallbacks so a box that already exports them for
    # the Python backend needs no duplicate HERMES_-prefixed vars.
    HERMES_DASHBOARD_SUPABASE_URL         (fallback: SUPABASE_URL)
    HERMES_DASHBOARD_SUPABASE_ANON_KEY    (fallback: SUPABASE_ANON_KEY)
    HERMES_DASHBOARD_SUPABASE_JWT_SECRET  (fallback: SUPABASE_JWT_SECRET, GOTRUE_JWT_SECRET)
    HERMES_DASHBOARD_SUPABASE_AUDIENCE    (optional; defaults to "authenticated")

Skip reasons: when the plugin loads but can't register (missing url / anon_key
/ jwt_secret), it writes a human-readable reason to the module-level
:data:`LAST_SKIP_REASON` so the gate's fail-closed branch can surface a useful
operator error instead of the bare "no providers registered".
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from typing import Any, Optional

import httpx

from hermes_cli.dashboard_auth import (
    DashboardAuthProvider,
    InvalidCredentialsError,
    LoginStart,
    ProviderError,
    RefreshExpiredError,
    Session,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------------

# GoTrue signs access tokens with HS256 (shared jwt_secret) by default.
_ALLOWED_ALGS = ("HS256",)

# GoTrue stamps every access token with aud="authenticated" unless the project
# overrides it. Pinned on verify so a token minted for a different audience is
# rejected.
_DEFAULT_AUDIENCE = "authenticated"

# httpx timeout for the token-endpoint round trips.
_TOKEN_TIMEOUT_SEC = 10.0


# ---------------------------------------------------------------------------
# Skip-reason channel (mirrors the basic / self-hosted plugins)
# ---------------------------------------------------------------------------

LAST_SKIP_REASON: str = ""


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class SupabaseAuthProvider(DashboardAuthProvider):
    """Email/password provider backed by Supabase Auth (GoTrue)."""

    name = "supabase"
    display_name = "Email & Password (Supabase)"
    supports_password = True

    def __init__(
        self,
        *,
        url: str,
        anon_key: str,
        jwt_secret: str,
        audience: str = _DEFAULT_AUDIENCE,
    ) -> None:
        if not url:
            raise ValueError("url is required")
        if not anon_key:
            raise ValueError("anon_key is required")
        if not jwt_secret:
            raise ValueError("jwt_secret is required")
        # Normalise the base URL and reject cleartext (except loopback) — the
        # password and tokens flow over it.
        self._base = url.rstrip("/")
        self._require_https_or_loopback(self._base)
        self._anon_key = anon_key
        self._jwt_secret = jwt_secret
        self._audience = (audience or "").strip() or _DEFAULT_AUDIENCE

    # ---- OAuth methods: not used (pure-password provider) ------------------

    def start_login(self, *, redirect_uri: str) -> LoginStart:
        raise NotImplementedError(
            "SupabaseAuthProvider is password-only; there is no OAuth redirect "
            "flow. The login page POSTs to /auth/password-login instead."
        )

    def complete_login(
        self, *, code: str, state: str, code_verifier: str, redirect_uri: str
    ) -> Session:
        raise NotImplementedError(
            "SupabaseAuthProvider is password-only; use "
            "complete_password_login."
        )

    # ---- password login ----------------------------------------------------

    def complete_password_login(
        self, *, username: str, password: str
    ) -> Session:
        """Exchange email (``username``) + password at GoTrue's password grant.

        The login form's ``username`` field carries the member's email —
        GoTrue authenticates by email. A rejected credential surfaces as
        :class:`InvalidCredentialsError` (→ generic 401); an unreachable server
        as :class:`ProviderError` (→ 503).
        """
        payload = self._token_grant(
            {"grant_type": "password"},
            {"email": username, "password": password},
            bad_request_exc=InvalidCredentialsError,
        )
        return self._session_from_grant(payload)

    # ---- session lifecycle -------------------------------------------------

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        # Local HS256 verification — no network. Returns None on any
        # expiry/invalidity (middleware then refreshes or logs out). There is
        # no ProviderError path here because verification is entirely local.
        claims = self._verify_access_token(access_token)
        if claims is None:
            return None
        return self._session_from_claims(
            access_token=access_token, refresh_token="", claims=claims
        )

    def refresh_session(self, *, refresh_token: str) -> Session:
        if not refresh_token:
            raise RefreshExpiredError("no refresh token present in session")
        payload = self._token_grant(
            {"grant_type": "refresh_token"},
            {"refresh_token": refresh_token},
            bad_request_exc=RefreshExpiredError,
            previous_refresh_token=refresh_token,
        )
        return self._session_from_grant(payload)

    def revoke_session(self, *, refresh_token: str) -> None:
        # GoTrue's /logout revokes by the *access* token, which this method
        # isn't given (the middleware clears the session cookies regardless).
        # Best-effort no-op — must never raise. The refresh token then simply
        # ages out at the server's configured lifetime.
        _ = refresh_token
        return None

    # ---- internals: token endpoint ----------------------------------------

    def _token_url(self, query: dict[str, str]) -> str:
        return f"{self._base}/auth/v1/token?{urllib.parse.urlencode(query)}"

    def _token_grant(
        self,
        query: dict[str, str],
        body: dict[str, str],
        *,
        bad_request_exc: type[Exception],
        previous_refresh_token: str = "",
    ) -> dict[str, Any]:
        """POST a GoTrue token grant and return the parsed JSON body.

        Shared by the password grant (``complete_password_login``) and the
        refresh grant (``refresh_session``). ``bad_request_exc`` maps a 400/401
        (credential/refresh rejected) to the caller's error type, preserving
        the middleware's distinct handling (401 vs forced re-login).
        """
        headers = {
            "apikey": self._anon_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = httpx.post(
                self._token_url(query),
                json=body,
                headers=headers,
                timeout=_TOKEN_TIMEOUT_SEC,
            )
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Supabase auth endpoint unreachable: {exc}"
            ) from exc

        # GoTrue returns 400 (password grant) or 400/401 (refresh) for a
        # rejected credential/token.
        if response.status_code in (400, 401, 403):
            raise bad_request_exc(
                f"Supabase rejected the token request "
                f"({response.status_code})"
            )
        if response.status_code != 200:
            raise ProviderError(
                f"Supabase token endpoint returned {response.status_code}: "
                f"{response.text[:200]!r}"
            )

        payload = self._parse_json_body(response)
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ProviderError(
                "Supabase token response missing access_token"
            )
        # Refresh-token rotation: GoTrue rotates the refresh token on the
        # refresh grant; keep the previous only if the response omits one.
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            payload["refresh_token"] = previous_refresh_token or ""
        return payload

    def _session_from_grant(self, payload: dict[str, Any]) -> Session:
        access_token = str(payload["access_token"])
        refresh_token = str(payload.get("refresh_token") or "")
        claims = self._verify_access_token(access_token)
        if claims is None:
            # The token was just minted by the server we trust; a failure here
            # means the configured jwt_secret/audience doesn't match the
            # server — an operator misconfiguration, not a bad credential.
            raise ProviderError(
                "Supabase issued a token this provider could not verify — "
                "check that dashboard.supabase_auth.jwt_secret matches the "
                "GoTrue server's GOTRUE_JWT_SECRET and that the audience is "
                f"{self._audience!r}."
            )
        return self._session_from_claims(
            access_token=access_token,
            refresh_token=refresh_token,
            claims=claims,
        )

    # ---- internals: JWT verification --------------------------------------

    def _verify_access_token(self, access_token: str) -> Optional[dict[str, Any]]:
        """Verify a GoTrue HS256 access token; return claims or ``None``.

        Returns ``None`` for an expired, tampered, wrong-audience, or otherwise
        invalid token (the caller treats that as "no valid session"). Never
        raises — verification is local, so there is no outage to surface.
        """
        import jwt  # lazy import — keeps startup fast for the ungated path

        try:
            return jwt.decode(
                access_token,
                self._jwt_secret,
                algorithms=list(_ALLOWED_ALGS),
                audience=self._audience,
                options={"require": ["exp", "sub"]},
            )
        except jwt.InvalidTokenError as exc:
            logger.debug("supabase auth: access token rejected: %s", exc)
            return None

    def _session_from_claims(
        self,
        *,
        access_token: str,
        refresh_token: str,
        claims: dict[str, Any],
    ) -> Session:
        """Map verified GoTrue claims onto a Session.

        The verified access token is stored in ``Session.access_token`` so the
        per-request ``verify_session`` re-verifies a real JWT.
        """
        user_id = str(claims.get("sub", ""))
        if not user_id:
            raise ProviderError("Supabase token missing 'sub' (user_id) claim")

        email = str(claims.get("email", "") or "")
        # GoTrue nests profile fields under user_metadata; fall back to email.
        metadata = claims.get("user_metadata")
        display_name = ""
        if isinstance(metadata, dict):
            display_name = str(
                metadata.get("name")
                or metadata.get("full_name")
                or metadata.get("display_name")
                or ""
            )
        display_name = display_name or email or user_id

        return Session(
            user_id=user_id,
            email=email,
            display_name=display_name,
            # Supabase has no first-class org/tenant on the token; leave blank.
            org_id="",
            provider=self.name,
            expires_at=int(claims["exp"]),
            access_token=access_token,
            refresh_token=refresh_token,
        )

    # ---- internals: misc ---------------------------------------------------

    @staticmethod
    def _require_https_or_loopback(url: str) -> None:
        """Reject a cleartext base URL that isn't loopback.

        The password and tokens flow over this URL, so require HTTPS except for
        an explicit loopback host (self-hosted dev / same-box Kong).
        """
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

    def _parse_json_body(self, response: httpx.Response) -> dict[str, Any]:
        ctype = response.headers.get("content-type", "")
        if "application/json" not in ctype:
            return {}
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def _load_config_supabase_auth_section() -> dict:
    """Return ``dashboard.supabase_auth`` from config.yaml, or ``{}``.

    Robust to load_config() raising, the keys being absent, or the value not
    being a dict — every shape falls through to ``{}``.
    """
    try:
        from hermes_cli.config import cfg_get, load_config

        cfg = load_config()
    except Exception as exc:  # noqa: BLE001 — broad catch is intentional
        logger.debug(
            "dashboard-auth-supabase: load_config() raised %s; "
            "falling back to env-only configuration",
            exc,
        )
        return {}
    section = cfg_get(cfg, "dashboard", "supabase_auth", default=None)
    return section if isinstance(section, dict) else {}


def _resolve(cfg_section: dict, cfg_key: str, *env_names: str) -> str:
    """env-wins-over-config resolution; empty env treated as unset.

    Tries each ``env_name`` in order (first non-empty wins) so a box that
    already exports the bare ``SUPABASE_*`` names needs no duplicate
    ``HERMES_``-prefixed vars, then falls back to config.yaml.
    """
    for env_name in env_names:
        env = os.environ.get(env_name, "").strip()
        if env:
            return env
    return str(cfg_section.get(cfg_key, "") or "").strip()


def register(ctx) -> None:
    """Plugin entry — registers SupabaseAuthProvider when configured.

    Registers only when a url + anon_key + jwt_secret are configured (via the
    ``HERMES_DASHBOARD_SUPABASE_*`` env vars, the bare ``SUPABASE_*`` fallbacks,
    or the ``dashboard.supabase_auth`` block in config.yaml). Operators not
    using Supabase leave these unset, so the plugin is a no-op for them.

    On skip, writes a reason to :data:`LAST_SKIP_REASON` naming both
    configuration surfaces.
    """
    global LAST_SKIP_REASON
    LAST_SKIP_REASON = ""

    section = _load_config_supabase_auth_section()
    url = _resolve(
        section, "url", "HERMES_DASHBOARD_SUPABASE_URL", "SUPABASE_URL"
    )
    anon_key = _resolve(
        section,
        "anon_key",
        "HERMES_DASHBOARD_SUPABASE_ANON_KEY",
        "SUPABASE_ANON_KEY",
    )
    jwt_secret = _resolve(
        section,
        "jwt_secret",
        "HERMES_DASHBOARD_SUPABASE_JWT_SECRET",
        "SUPABASE_JWT_SECRET",
        "GOTRUE_JWT_SECRET",
    )
    audience = (
        _resolve(section, "audience", "HERMES_DASHBOARD_SUPABASE_AUDIENCE")
        or _DEFAULT_AUDIENCE
    )

    missing = [
        label
        for label, value in (
            ("url", url),
            ("anon_key", anon_key),
            ("jwt_secret", jwt_secret),
        )
        if not value
    ]
    if missing:
        LAST_SKIP_REASON = (
            "Supabase dashboard auth is not configured. Set url, anon_key and "
            "jwt_secret — either under dashboard.supabase_auth in config.yaml "
            "or via the HERMES_DASHBOARD_SUPABASE_URL / _ANON_KEY / _JWT_SECRET "
            "env vars (the bare SUPABASE_URL / SUPABASE_ANON_KEY / "
            "SUPABASE_JWT_SECRET / GOTRUE_JWT_SECRET names are recognised as "
            "fallbacks) — or use another provider / pass --insecure. "
            f"(missing: {', '.join(missing)})"
        )
        logger.debug("dashboard-auth-supabase: %s", LAST_SKIP_REASON)
        return

    try:
        provider = SupabaseAuthProvider(
            url=url,
            anon_key=anon_key,
            jwt_secret=jwt_secret,
            audience=audience,
        )
    except ValueError as exc:
        LAST_SKIP_REASON = f"SupabaseAuthProvider construction failed: {exc}"
        logger.warning("dashboard-auth-supabase: %s", LAST_SKIP_REASON)
        return

    ctx.register_dashboard_auth_provider(provider)
    logger.info(
        "dashboard-auth-supabase: registered password provider "
        "(url=%s, audience=%s)",
        url,
        audience,
    )
