"""FG-15 — E2E readiness tests against a real temp ``HERMES_HOME``.

Exercises the real config.yaml / .env round-trip and the shared readiness
backend with real imports (no mocks of the config layer). Also asserts the
"no new non-secret ``HERMES_*`` env var" invariant and that the datastore fix
routes the credential to ``.env`` while behavioural state stays in
``config.yaml``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hermes_cli import onboarding_readiness as R
from hermes_cli.onboarding_readiness import evaluate, seen_flag


@pytest.fixture()
def temp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Start from a clean secret environment so presence checks are deterministic.
    for name in (
        *R.LLM_PROVIDER_SECRET_ENV_VARS,
        R.TELEGRAM_TOKEN_ENV_VAR,
        R.TELEGRAM_HOME_CHANNEL_ENV_VAR,
        "DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    return home


# ---------------------------------------------------------------------------
# Real-path readiness
# ---------------------------------------------------------------------------

def test_fresh_home_is_not_ready(temp_home: Path, monkeypatch) -> None:
    from hermes_cli.config import save_config

    save_config({"onboarding": {"seen": {}}}, strip_defaults=False)
    readiness = evaluate(include_owner=False)
    # config.yaml now exists → home bootstrap met; everything else unmet.
    home_item = next(r for r in readiness.results if r.item.key == R.HOME_BOOTSTRAP)
    assert home_item.met is True
    assert readiness.ready_for_prod is False
    assert readiness.required_met == 1


def test_secrets_from_env_flip_required_items(temp_home: Path, monkeypatch) -> None:
    from hermes_cli.config import save_config

    save_config({"onboarding": {"seen": {}}}, strip_defaults=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-xxx")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "987654321")

    readiness = evaluate(include_owner=False)
    met = {r.item.key for r in readiness.results if r.met}
    assert R.LLM_PROVIDER_SECRET in met
    assert R.TELEGRAM_CHANNEL in met
    assert R.HOME_BOOTSTRAP in met


def test_app_dsn_resolves_through_env_ref(temp_home: Path, monkeypatch) -> None:
    from hermes_cli.config import save_config

    save_config(
        {"datastore": {"supabase_app": {"dsn": "${DATABASE_URL}"}}},
        strip_defaults=False,
    )
    # Unresolved ${DATABASE_URL} must NOT count as configured.
    assert R.app_datastore_configured() is False
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/db")
    # Config is cached on file mtime/size, not env — drop the cache so the
    # env-ref re-expands against the now-set DATABASE_URL (a fresh CLI process
    # would load it expanded from the start).
    from hermes_cli import config as cfg

    cfg._LOAD_CONFIG_CACHE.clear()
    cfg._RAW_CONFIG_CACHE.clear()
    assert R.app_datastore_configured() is True


def test_app_dsn_present_when_config_holds_unexpanded_ref(
    temp_home: Path, monkeypatch
) -> None:
    # Mirrors the wizard's fix action: it stores the literal "${DATABASE_URL}"
    # in the in-memory config and writes the value to .env / os.environ. The
    # readiness check must expand the ref against the live env.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    config = {"datastore": {"supabase_app": {"dsn": "${DATABASE_URL}"}}}
    assert R.app_datastore_configured(config) is True
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert R.app_datastore_configured(config) is False


def test_optional_items_from_config(temp_home: Path) -> None:
    from hermes_cli.config import save_config

    save_config(
        {
            "memory": {"provider": "mem0"},
            "toolsets": ["hermes-cli", "web-tools"],
        },
        strip_defaults=False,
    )
    readiness = evaluate(include_owner=False)
    by_key = {r.item.key: r for r in readiness.results}
    assert by_key[R.MEMORY_PROVIDER].met is True
    assert by_key[R.EXTRA_TOOLS].met is True
    # Optional coverage does not gate prod.
    assert readiness.ready_for_prod is False


def test_readiness_is_read_only_and_idempotent(temp_home: Path) -> None:
    from hermes_cli.config import get_config_path, save_config

    save_config({"onboarding": {"seen": {}}, "agent": {"x": 1}}, strip_defaults=False)
    before = get_config_path().read_text(encoding="utf-8")
    first = evaluate(include_owner=False).as_dict()
    second = evaluate(include_owner=False).as_dict()
    after = get_config_path().read_text(encoding="utf-8")
    assert first == second  # deterministic
    assert before == after  # evaluate() never mutates config


# ---------------------------------------------------------------------------
# onboarding.seen resumability (reuse the existing flag store)
# ---------------------------------------------------------------------------

def test_seen_flag_round_trips_via_agent_onboarding(temp_home: Path) -> None:
    from agent.onboarding import is_seen, mark_seen
    from hermes_cli.config import get_config_path, load_config, save_config

    save_config({"onboarding": {"seen": {}}}, strip_defaults=False)
    config_path = get_config_path()
    flag = seen_flag(R.LLM_PROVIDER_SECRET)

    assert is_seen(load_config(), flag) is False
    assert mark_seen(config_path, flag) is True
    assert is_seen(load_config(), flag) is True
    # Idempotent: marking an already-set flag succeeds and preserves state.
    assert mark_seen(config_path, flag) is True
    assert load_config().get("onboarding", {}).get("seen", {}).get(flag) is True


# ---------------------------------------------------------------------------
# Secrets → .env, behaviour → config.yaml; no new non-secret HERMES_* env var
# ---------------------------------------------------------------------------

def test_datastore_fix_writes_secret_to_env_and_ref_to_config(
    temp_home: Path, monkeypatch
) -> None:
    from hermes_cli import setup as setup_mod
    from hermes_cli.config import get_config_path, get_env_path, load_config, save_config

    save_config({"onboarding": {"seen": {}}}, strip_defaults=False)
    monkeypatch.setattr(
        setup_mod, "masked_secret_prompt", lambda *_a, **_k: "postgresql://u:p@h/db"
    )
    config = load_config()
    setup_mod._essentials_fix_datastore(config)

    # Credential goes to .env — never to config.yaml.
    env_text = get_env_path().read_text(encoding="utf-8")
    assert "DATABASE_URL=" in env_text
    assert "postgresql://u:p@h/db" in env_text
    config_text = get_config_path().read_text(encoding="utf-8")
    assert "postgresql://u:p@h/db" not in config_text
    # Behavioural reference stays in config.yaml as an env-ref template.
    assert config["datastore"]["supabase_app"]["dsn"] == "${DATABASE_URL}"


def test_no_new_non_secret_hermes_env_var() -> None:
    # 1. Every env var the schema keys off is a provider/channel CREDENTIAL —
    #    none is a behavioural HERMES_* var.
    all_env_names = (
        *R.LLM_PROVIDER_SECRET_ENV_VARS,
        *R.ADDITIONAL_CHANNEL_ENV_VARS,
        R.TELEGRAM_TOKEN_ENV_VAR,
        R.TELEGRAM_HOME_CHANNEL_ENV_VAR,
    )
    assert not any(name.startswith("HERMES_") for name in all_env_names)

    # 2. The new readiness backend introduces no HERMES_* env-var literal
    #    (HERMES_HOME is reached via get_hermes_home(), not a literal here).
    source = Path(R.__file__).read_text(encoding="utf-8")
    assert not re.search(r"[\"']HERMES_[A-Z0-9_]+[\"']", source)
