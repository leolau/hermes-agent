"""Tests for the bundled Supabase (GoTrue) email/password dashboard-auth plugin.

Covers, by analogy with ``test_basic_provider.py`` / ``test_self_hosted_provider.py``:

1. Construction validation (required url/anon_key/jwt_secret, https enforcement).
2. ``complete_password_login`` — httpx-mocked GoTrue password grant, happy path
   (claims → Session), and error mapping (401 → InvalidCredentialsError,
   network → ProviderError).
3. ``verify_session`` — local HS256 verification: valid token, expiry → None,
   tampered/wrong-secret → None, wrong-audience → None.
4. ``refresh_session`` — refresh grant rotation + error mapping;
   ``revoke_session`` best-effort no-op.
5. ``register(ctx)`` — env/config precedence, SUPABASE_* fallbacks, skip reasons.

All HTTP is mocked; only the JWT crypto is real (HS256, local).
"""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import jwt
import pytest

import plugins.dashboard_auth.supabase as supabase_plugin
from hermes_cli.dashboard_auth import (
    InvalidCredentialsError,
    ProviderError,
    RefreshExpiredError,
    Session,
    assert_protocol_compliance,
)

_URL = "https://supabase.example.com"
_ANON = "anon-key-abc"
_SECRET = "super-secret-gotrue-jwt-signing-key-at-least-32-bytes-long"
_AUD = "authenticated"
_SUB = "a1b2c3d4-0000-4000-8000-000000000001"


def _mint_access_token(
    *,
    secret: str = _SECRET,
    aud: str = _AUD,
    sub: str | None = _SUB,
    email: str | None = "member@example.com",
    user_metadata: dict[str, Any] | None = None,
    ttl_seconds: int = 3600,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "aud": aud,
        "iat": now,
        "exp": now + ttl_seconds,
        "role": "authenticated",
    }
    if sub is not None:
        claims["sub"] = sub
    if email is not None:
        claims["email"] = email
    if user_metadata is not None:
        claims["user_metadata"] = user_metadata
    return jwt.encode(claims, secret, algorithm="HS256")


def _make_provider(**overrides) -> supabase_plugin.SupabaseAuthProvider:
    kwargs = {
        "url": _URL,
        "anon_key": _ANON,
        "jwt_secret": _SECRET,
    }
    kwargs.update(overrides)
    return supabase_plugin.SupabaseAuthProvider(**kwargs)


def _mock_post(status_code: int, body: Any, *, ctype: str = "application/json"):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if isinstance(body, dict):
        resp.text = json.dumps(body)
        resp.json = MagicMock(return_value=body)
    else:
        resp.text = str(body)
        resp.json = MagicMock(side_effect=ValueError("not json"))
    resp.headers = {"content-type": ctype}
    return resp


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_protocol_compliance(self):
        assert_protocol_compliance(supabase_plugin.SupabaseAuthProvider)

    def test_name_and_display(self):
        p = _make_provider()
        assert p.name == "supabase"
        assert p.display_name == "Email & Password (Supabase)"
        assert p.supports_password is True

    def test_strips_trailing_slash(self):
        assert _make_provider(url=_URL + "/")._base == _URL

    def test_requires_url(self):
        with pytest.raises(ValueError, match="url"):
            _make_provider(url="")

    def test_requires_anon_key(self):
        with pytest.raises(ValueError, match="anon_key"):
            _make_provider(anon_key="")

    def test_requires_jwt_secret(self):
        with pytest.raises(ValueError, match="jwt_secret"):
            _make_provider(jwt_secret="")

    def test_rejects_non_https(self):
        with pytest.raises(ValueError, match="https"):
            _make_provider(url="http://supabase.example.com")

    def test_allows_http_localhost(self):
        assert _make_provider(url="http://127.0.0.1:8000")._base == (
            "http://127.0.0.1:8000"
        )

    def test_oauth_methods_not_implemented(self):
        p = _make_provider()
        with pytest.raises(NotImplementedError):
            p.start_login(redirect_uri="https://x/auth/callback")
        with pytest.raises(NotImplementedError):
            p.complete_login(
                code="c", state="s", code_verifier="v", redirect_uri="r"
            )


# ---------------------------------------------------------------------------
# complete_password_login
# ---------------------------------------------------------------------------


class TestPasswordLogin:
    def test_happy_path_maps_claims_to_session(self):
        p = _make_provider()
        access = _mint_access_token(
            user_metadata={"name": "A Member"}
        )
        body = {
            "access_token": access,
            "refresh_token": "refresh-xyz",
            "token_type": "bearer",
        }
        with patch(
            "plugins.dashboard_auth.supabase.httpx.post",
            return_value=_mock_post(200, body),
        ) as mock_post:
            session = p.complete_password_login(
                username="member@example.com", password="pw"
            )
        assert isinstance(session, Session)
        assert session.user_id == _SUB
        assert session.email == "member@example.com"
        assert session.display_name == "A Member"
        assert session.provider == "supabase"
        assert session.access_token == access
        assert session.refresh_token == "refresh-xyz"
        # The request hit the password grant with the email in the body and the
        # anon key in the apikey header.
        call = mock_post.call_args
        url = call.args[0]
        assert url.startswith(f"{_URL}/auth/v1/token?")
        assert dict(
            urllib.parse.parse_qsl(urllib.parse.urlparse(url).query)
        ) == {"grant_type": "password"}
        assert call.kwargs["json"] == {
            "email": "member@example.com",
            "password": "pw",
        }
        assert call.kwargs["headers"]["apikey"] == _ANON

    def test_display_name_falls_back_to_email(self):
        p = _make_provider()
        access = _mint_access_token(user_metadata={})
        body = {"access_token": access, "refresh_token": "r"}
        with patch(
            "plugins.dashboard_auth.supabase.httpx.post",
            return_value=_mock_post(200, body),
        ):
            session = p.complete_password_login(
                username="member@example.com", password="pw"
            )
        assert session.display_name == "member@example.com"

    def test_bad_credentials_maps_to_invalid_credentials(self):
        p = _make_provider()
        for status in (400, 401, 403):
            with patch(
                "plugins.dashboard_auth.supabase.httpx.post",
                return_value=_mock_post(
                    status, {"error": "invalid_grant"}
                ),
            ):
                with pytest.raises(InvalidCredentialsError):
                    p.complete_password_login(
                        username="member@example.com", password="wrong"
                    )

    def test_server_error_maps_to_provider_error(self):
        p = _make_provider()
        with patch(
            "plugins.dashboard_auth.supabase.httpx.post",
            return_value=_mock_post(500, "boom"),
        ):
            with pytest.raises(ProviderError):
                p.complete_password_login(
                    username="member@example.com", password="pw"
                )

    def test_network_error_maps_to_provider_error(self):
        p = _make_provider()
        with patch(
            "plugins.dashboard_auth.supabase.httpx.post",
            side_effect=httpx.ConnectError("no route"),
        ):
            with pytest.raises(ProviderError, match="unreachable"):
                p.complete_password_login(
                    username="member@example.com", password="pw"
                )

    def test_missing_access_token_raises(self):
        p = _make_provider()
        with patch(
            "plugins.dashboard_auth.supabase.httpx.post",
            return_value=_mock_post(200, {"refresh_token": "r"}),
        ):
            with pytest.raises(ProviderError, match="access_token"):
                p.complete_password_login(
                    username="member@example.com", password="pw"
                )

    def test_token_verify_failure_is_operator_error(self):
        # The server returned a token this provider can't verify (jwt_secret
        # mismatch) → ProviderError, not a bad-credential 401.
        p = _make_provider()
        access = _mint_access_token(
            secret="a-different-secret-also-at-least-32-bytes-long"
        )
        with patch(
            "plugins.dashboard_auth.supabase.httpx.post",
            return_value=_mock_post(200, {"access_token": access}),
        ):
            with pytest.raises(ProviderError, match="jwt_secret"):
                p.complete_password_login(
                    username="member@example.com", password="pw"
                )


# ---------------------------------------------------------------------------
# verify_session (local HS256)
# ---------------------------------------------------------------------------


class TestVerifySession:
    def test_valid_token_returns_session(self):
        p = _make_provider()
        access = _mint_access_token()
        session = p.verify_session(access_token=access)
        assert session is not None
        assert session.user_id == _SUB
        assert session.email == "member@example.com"

    def test_expired_token_returns_none(self):
        p = _make_provider()
        access = _mint_access_token(ttl_seconds=-10)
        assert p.verify_session(access_token=access) is None

    def test_wrong_secret_returns_none(self):
        p = _make_provider()
        access = _mint_access_token(
            secret="not-the-real-secret-but-still-32-plus-bytes-long"
        )
        assert p.verify_session(access_token=access) is None

    def test_wrong_audience_returns_none(self):
        p = _make_provider()
        access = _mint_access_token(aud="some-other-audience")
        assert p.verify_session(access_token=access) is None

    def test_garbage_token_returns_none(self):
        p = _make_provider()
        assert p.verify_session(access_token="not.a.jwt") is None

    def test_custom_audience_is_pinned(self):
        p = _make_provider(audience="dashboard")
        good = _mint_access_token(aud="dashboard")
        assert p.verify_session(access_token=good) is not None
        # The default audience token is now rejected.
        assert p.verify_session(access_token=_mint_access_token()) is None


# ---------------------------------------------------------------------------
# refresh_session / revoke_session
# ---------------------------------------------------------------------------


class TestRefreshRevoke:
    def test_refresh_rotates_tokens(self):
        p = _make_provider()
        access = _mint_access_token()
        body = {"access_token": access, "refresh_token": "rotated-refresh"}
        with patch(
            "plugins.dashboard_auth.supabase.httpx.post",
            return_value=_mock_post(200, body),
        ) as mock_post:
            session = p.refresh_session(refresh_token="old-refresh")
        assert session.refresh_token == "rotated-refresh"
        url = mock_post.call_args.args[0]
        assert dict(
            urllib.parse.parse_qsl(urllib.parse.urlparse(url).query)
        ) == {"grant_type": "refresh_token"}
        assert mock_post.call_args.kwargs["json"] == {
            "refresh_token": "old-refresh"
        }

    def test_refresh_keeps_previous_when_response_omits_one(self):
        p = _make_provider()
        body = {"access_token": _mint_access_token()}
        with patch(
            "plugins.dashboard_auth.supabase.httpx.post",
            return_value=_mock_post(200, body),
        ):
            session = p.refresh_session(refresh_token="keep-me")
        assert session.refresh_token == "keep-me"

    def test_empty_refresh_token_raises(self):
        p = _make_provider()
        with pytest.raises(RefreshExpiredError):
            p.refresh_session(refresh_token="")

    def test_rejected_refresh_maps_to_expired(self):
        p = _make_provider()
        with patch(
            "plugins.dashboard_auth.supabase.httpx.post",
            return_value=_mock_post(400, {"error": "invalid_grant"}),
        ):
            with pytest.raises(RefreshExpiredError):
                p.refresh_session(refresh_token="dead")

    def test_revoke_is_noop(self):
        p = _make_provider()
        # Must not raise and must not make a network call.
        with patch("plugins.dashboard_auth.supabase.httpx.post") as mock_post:
            assert p.revoke_session(refresh_token="whatever") is None
            mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# register() entry point
# ---------------------------------------------------------------------------


class TestRegister:
    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        for var in (
            "HERMES_DASHBOARD_SUPABASE_URL",
            "HERMES_DASHBOARD_SUPABASE_ANON_KEY",
            "HERMES_DASHBOARD_SUPABASE_JWT_SECRET",
            "HERMES_DASHBOARD_SUPABASE_AUDIENCE",
            "SUPABASE_URL",
            "SUPABASE_ANON_KEY",
            "SUPABASE_JWT_SECRET",
            "GOTRUE_JWT_SECRET",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(
            supabase_plugin,
            "_load_config_supabase_auth_section",
            lambda: {},
        )

    def test_skips_when_unconfigured(self, monkeypatch):
        ctx = MagicMock()
        supabase_plugin.register(ctx)
        ctx.register_dashboard_auth_provider.assert_not_called()
        assert "missing" in supabase_plugin.LAST_SKIP_REASON
        assert "url" in supabase_plugin.LAST_SKIP_REASON

    def test_skips_when_only_partial(self, monkeypatch):
        monkeypatch.setenv("HERMES_DASHBOARD_SUPABASE_URL", _URL)
        monkeypatch.setenv("HERMES_DASHBOARD_SUPABASE_ANON_KEY", _ANON)
        ctx = MagicMock()
        supabase_plugin.register(ctx)
        ctx.register_dashboard_auth_provider.assert_not_called()
        assert "jwt_secret" in supabase_plugin.LAST_SKIP_REASON

    def test_registers_from_hermes_env(self, monkeypatch):
        monkeypatch.setenv("HERMES_DASHBOARD_SUPABASE_URL", _URL)
        monkeypatch.setenv("HERMES_DASHBOARD_SUPABASE_ANON_KEY", _ANON)
        monkeypatch.setenv("HERMES_DASHBOARD_SUPABASE_JWT_SECRET", _SECRET)
        ctx = MagicMock()
        supabase_plugin.register(ctx)
        ctx.register_dashboard_auth_provider.assert_called_once()
        provider = ctx.register_dashboard_auth_provider.call_args.args[0]
        assert isinstance(provider, supabase_plugin.SupabaseAuthProvider)
        assert supabase_plugin.LAST_SKIP_REASON == ""
        # Locally verifies a token the same secret minted.
        assert provider.verify_session(
            access_token=_mint_access_token()
        ) is not None

    def test_bare_supabase_env_fallbacks(self, monkeypatch):
        # A box that already exports SUPABASE_* / GOTRUE_JWT_SECRET needs no
        # HERMES_-prefixed duplicates.
        monkeypatch.setenv("SUPABASE_URL", _URL)
        monkeypatch.setenv("SUPABASE_ANON_KEY", _ANON)
        monkeypatch.setenv("GOTRUE_JWT_SECRET", _SECRET)
        ctx = MagicMock()
        supabase_plugin.register(ctx)
        ctx.register_dashboard_auth_provider.assert_called_once()

    def test_hermes_env_wins_over_config(self, monkeypatch):
        monkeypatch.setattr(
            supabase_plugin,
            "_load_config_supabase_auth_section",
            lambda: {
                "url": "https://config.example.com",
                "anon_key": "cfg",
                "jwt_secret": "cfg-secret",
            },
        )
        monkeypatch.setenv("HERMES_DASHBOARD_SUPABASE_URL", _URL)
        ctx = MagicMock()
        supabase_plugin.register(ctx)
        provider = ctx.register_dashboard_auth_provider.call_args.args[0]
        assert provider._base == _URL  # env url won
        assert provider._anon_key == "cfg"  # config anon_key used

    def test_registers_from_config(self, monkeypatch):
        monkeypatch.setattr(
            supabase_plugin,
            "_load_config_supabase_auth_section",
            lambda: {
                "url": _URL,
                "anon_key": _ANON,
                "jwt_secret": _SECRET,
                "audience": "dashboard",
            },
        )
        ctx = MagicMock()
        supabase_plugin.register(ctx)
        provider = ctx.register_dashboard_auth_provider.call_args.args[0]
        assert provider._audience == "dashboard"
