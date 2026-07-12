"""FG-03 live-gateway wiring — the inbound chokepoint enriches channel identity.

These tests pin the *runtime* seam that PR-#24 documented as outstanding: the
live gateway (`gateway/run.py`) must stamp the receiving ``account_id`` and
resolve the channel sender to an internal principal **before** it derives the
session key, so multi-account / per-internal-user isolation is active for real
channels (WhatsApp, email, …) — not just in the standalone
``InboundRouter`` contract.

They exercise the real code paths:
* ``BasePlatformAdapter.account_id`` + ``build_source`` (config-driven, no new
  env var; ``None`` default keeps single-account keys byte-identical).
* ``GatewayRunner._enrich_channel_source_identity`` — stamps ``account_id`` from
  the receiving adapter, resolves the internal user via the C1
  ``bind_channel_principal`` seam, is a no-op when neither is configured, and
  never raises into the message path.
* ``GatewayRunner._get_principal_store`` — gated on a configured Supabase DSN.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import gateway.inbound as inbound
from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.run import GatewayRunner
from gateway.session import SessionSource, build_session_key


class _StubAdapter(BasePlatformAdapter):
    """Minimal concrete adapter so the real ``account_id``/``build_source`` run."""

    def __init__(self, config: object, platform: Platform = Platform.WHATSAPP) -> None:
        self.config = config
        self.platform = platform

    async def connect(  # pragma: no cover - abstract stub
        self, *, is_reconnect: bool = False
    ) -> bool:
        return True

    async def disconnect(self) -> None:  # pragma: no cover - abstract stub
        return None

    async def get_chat_info(self, chat_id: str) -> dict:  # pragma: no cover
        return {}

    async def send(  # pragma: no cover - abstract stub
        self,
        chat_id: str,
        content: str,
        reply_to=None,
        metadata=None,
    ) -> SendResult:
        return SendResult(success=True)


def _source(platform: Platform, chat_id: str, user_id: str) -> SessionSource:
    return SessionSource(
        platform=platform, chat_id=chat_id, chat_type="dm", user_id=user_id
    )


def _bare_runner(adapters: dict, principal_store: object = None) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.adapters = adapters
    # Pre-seed the cache so _get_principal_store returns this store without
    # touching config/DB. ``None`` means "not configured" (the default gate).
    runner._principal_store_cache = principal_store
    return runner


# --- BasePlatformAdapter.account_id (config-driven, byte-stable default) -------


def test_adapter_account_id_defaults_none_and_reads_config() -> None:
    # No account configured -> None (single-account deployments unaffected).
    plain = _StubAdapter(SimpleNamespace(extra={}))
    assert plain.account_id is None
    # A byte-identical source is built when no account is set.
    src = plain.build_source(chat_id="c1", user_id="u1")
    assert src.account_id is None

    # Read from the config.extra convention (behavioral config, not a secret).
    configured = _StubAdapter(SimpleNamespace(extra={"account_id": "inbox@a.example"}))
    assert configured.account_id == "inbox@a.example"
    assert configured.build_source(chat_id="c1", user_id="u1").account_id == (
        "inbox@a.example"
    )

    # An explicit build_source override beats the adapter default.
    assert configured.build_source(
        chat_id="c1", user_id="u1", account_id="override"
    ).account_id == "override"


# --- _enrich_channel_source_identity: account_id stamping ----------------------


@pytest.mark.asyncio
async def test_enrich_stamps_account_id_from_receiving_adapter() -> None:
    adapter = _StubAdapter(SimpleNamespace(extra={"account_id": "wa_A"}))
    runner = _bare_runner({Platform.WHATSAPP: adapter}, principal_store=None)

    src = _source(Platform.WHATSAPP, "sender1", "sender1")
    enriched = await runner._enrich_channel_source_identity(src)

    assert enriched.account_id == "wa_A"
    # No principal store configured -> internal user stays unresolved.
    assert enriched.internal_user_id is None
    # The account dimension is now folded into the key.
    assert ":acct:" in build_session_key(enriched)


@pytest.mark.asyncio
async def test_two_accounts_same_sender_isolate_and_egress_diverges() -> None:
    """Same external sender into two of my WhatsApp numbers -> two sessions."""
    adapter_a = _StubAdapter(SimpleNamespace(extra={"account_id": "wa_A"}))
    adapter_b = _StubAdapter(SimpleNamespace(extra={"account_id": "wa_B"}))

    runner_a = _bare_runner({Platform.WHATSAPP: adapter_a})
    runner_b = _bare_runner({Platform.WHATSAPP: adapter_b})

    # Identical sender identity — only the receiving account differs.
    src_a = await runner_a._enrich_channel_source_identity(
        _source(Platform.WHATSAPP, "+15550000001", "+15550000001")
    )
    src_b = await runner_b._enrich_channel_source_identity(
        _source(Platform.WHATSAPP, "+15550000001", "+15550000001")
    )

    assert src_a.account_id == "wa_A"
    assert src_b.account_id == "wa_B"
    # Distinct session keys => distinct cached AIAgent cores => the reply for
    # each turn egresses via the account that received it.
    assert build_session_key(src_a) != build_session_key(src_b)


@pytest.mark.asyncio
async def test_enrich_is_noop_without_account_or_store() -> None:
    """No adapter account + no principal store => byte-identical key preserved."""
    adapter = _StubAdapter(SimpleNamespace(extra={}))  # account_id -> None
    runner = _bare_runner({Platform.WHATSAPP: adapter}, principal_store=None)

    src = _source(Platform.WHATSAPP, "sender1", "sender1")
    baseline = build_session_key(src)
    enriched = await runner._enrich_channel_source_identity(src)

    assert enriched.account_id is None
    assert enriched.internal_user_id is None
    assert build_session_key(enriched) == baseline


@pytest.mark.asyncio
async def test_enrich_preserves_preset_account_id() -> None:
    adapter = _StubAdapter(SimpleNamespace(extra={"account_id": "wa_ADAPTER"}))
    runner = _bare_runner({Platform.WHATSAPP: adapter})

    src = _source(Platform.WHATSAPP, "s", "s")
    src.account_id = "wa_PRESET"
    enriched = await runner._enrich_channel_source_identity(src)
    # A source that already carries an account (e.g. a producer set it) is not
    # overwritten by the adapter default.
    assert enriched.account_id == "wa_PRESET"


# --- _enrich_channel_source_identity: internal-user resolution -----------------


@pytest.mark.asyncio
async def test_enrich_resolves_internal_user_via_c1_seam(monkeypatch) -> None:
    adapter = _StubAdapter(SimpleNamespace(extra={"account_id": "wa_A"}))
    # A non-None sentinel store makes the runner attempt principal resolution.
    runner = _bare_runner({Platform.WHATSAPP: adapter}, principal_store=object())

    async def _fake_bind(source, *, store):  # matches bind_channel_principal
        source.internal_user_id = "internal-user-42"
        return SimpleNamespace(user_id="internal-user-42")

    monkeypatch.setattr(inbound, "bind_channel_principal", _fake_bind)

    src = _source(Platform.WHATSAPP, "15550007777", "15550007777")
    enriched = await runner._enrich_channel_source_identity(src)

    assert enriched.internal_user_id == "internal-user-42"
    key = build_session_key(enriched)
    assert ":usr:internal-user-42" in key
    assert ":acct:" in key


@pytest.mark.asyncio
async def test_enrich_swallows_resolution_errors(monkeypatch) -> None:
    adapter = _StubAdapter(SimpleNamespace(extra={"account_id": "wa_A"}))
    runner = _bare_runner({Platform.WHATSAPP: adapter}, principal_store=object())

    async def _boom(source, *, store):
        raise RuntimeError("db down")

    monkeypatch.setattr(inbound, "bind_channel_principal", _boom)

    src = _source(Platform.WHATSAPP, "15550007777", "15550007777")
    # A DB hiccup must never drop the live message — it falls back to
    # channel-identity keying with the account still stamped.
    enriched = await runner._enrich_channel_source_identity(src)
    assert enriched.internal_user_id is None
    assert enriched.account_id == "wa_A"


# --- _get_principal_store gating -----------------------------------------------


def test_principal_store_gated_on_configured_dsn(monkeypatch) -> None:
    runner = object.__new__(GatewayRunner)

    # ``load_config_readonly`` is imported inside the method from
    # ``hermes_cli.config`` — patch it there.
    import hermes_cli.config as hconfig

    # An unset DATABASE_URL leaves the ``${DATABASE_URL}`` placeholder intact,
    # which the gate treats as "app DB not configured".
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        hconfig,
        "load_config_readonly",
        lambda: {"datastore": {"supabase_app": {"dsn": "${DATABASE_URL}"}}},
    )
    assert runner._get_principal_store() is None

    # A concrete DSN yields a PrincipalStore (construction does not connect).
    runner2 = object.__new__(GatewayRunner)
    monkeypatch.setattr(
        hconfig,
        "load_config_readonly",
        lambda: {
            "datastore": {
                "supabase_app": {"dsn": "postgresql://u:p@127.0.0.1:5432/db"}
            }
        },
    )
    from hermes_cli.access import PrincipalStore

    store = runner2._get_principal_store()
    assert isinstance(store, PrincipalStore)
    # Cached: a second call returns the same object without re-reading config.
    assert runner2._get_principal_store() is store
