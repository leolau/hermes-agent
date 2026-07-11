"""Unit and local-file contract tests for the C3 datastore router."""

from pathlib import Path

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from hermes_cli.datastore import (
    SQLiteCoreStore,
    SupabaseAppStore,
    get_store,
    resolve_mode,
)


def test_mode_defaults_to_prod() -> None:
    assert resolve_mode(config={}) == "prod"
    store = get_store("sqlite-core", config={})
    assert store.mode == "prod"
    assert store.path.name == "state.db"


def test_explicit_dev_mode_uses_disposable_sqlite_store() -> None:
    assert resolve_mode(config={"datastore": {"mode": "dev"}}) == "dev"
    prod = get_store("sqlite-core", "prod", config={})
    dev = get_store("sqlite-core", "dev", config={})

    assert isinstance(prod, SQLiteCoreStore)
    assert isinstance(dev, SQLiteCoreStore)
    assert prod.path.parent == dev.path.parent
    assert prod.path.name == "state.db"
    assert dev.path.name == "state.dev.db"

    prod_db = prod.connect()
    dev_db = dev.connect()
    prod_db.close()
    dev_db.close()

    assert prod.path.exists()
    assert dev.path.exists()
    assert prod.path != dev.path


def test_supabase_store_resolves_locked_schema_names() -> None:
    config = {"datastore": {"supabase_app": {"dsn": "postgresql://example"}}}
    prod = get_store("supabase-app", "prod", config=config)
    dev = get_store("supabase-app", "dev", config=config)

    assert isinstance(prod, SupabaseAppStore)
    assert isinstance(dev, SupabaseAppStore)
    assert prod.schema == "app_prod"
    assert dev.schema == "app_dev"
    assert prod.dsn == dev.dsn


def test_supabase_store_honors_mode_specific_dsn_override() -> None:
    config = {
        "datastore": {
            "supabase_app": {"dsn": "postgresql://base"},
            "overrides": {
                "dev": {"supabase_app": {"dsn": "postgresql://dev"}},
                "prod": {"supabase_app": {"dsn": "postgresql://prod"}},
            },
        }
    }
    assert get_store("supabase-app", "dev", config=config).dsn.endswith("/dev")
    assert get_store("supabase-app", "prod", config=config).dsn.endswith("/prod")


@pytest.mark.parametrize("requested", ["dev", None])
def test_channel_origin_can_never_resolve_to_dev(requested: str | None) -> None:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="channel-123",
        chat_type="channel",
    )
    config = {"datastore": {"mode": "dev"}}

    assert resolve_mode(requested, source=source, config=config) == "prod"
    assert source.resolve_datastore_mode(requested) == "prod"
    assert get_store(
        "sqlite-core",
        requested,
        source=source,
        config=config,
    ).path.name == "state.db"


def test_local_session_can_explicitly_use_dev() -> None:
    source = SessionSource(platform=Platform.LOCAL, chat_id="local")
    assert resolve_mode("dev", source=source, config={}) == "dev"


def test_dashboard_api_session_can_explicitly_use_dev() -> None:
    source = SessionSource(platform=Platform.API_SERVER, chat_id="dashboard")
    assert resolve_mode("dev", source=source, config={}) == "dev"


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid datastore mode"):
        resolve_mode("preview", config={})


def test_store_paths_live_under_hermes_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert get_store("sqlite-core", "prod", config={}).path == tmp_path / "state.db"
    assert get_store("sqlite-core", "dev", config={}).path == tmp_path / "state.dev.db"
