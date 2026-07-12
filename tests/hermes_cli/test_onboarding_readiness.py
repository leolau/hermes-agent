"""FG-15 — unit tests for the typed onboarding setup schema + readiness score.

Pure/in-memory tests over the readiness backend: the required-vs-optional
schema, per-item checks, the readiness score, and the "ready for prod" gate.
Real-filesystem / temp ``HERMES_HOME`` behaviour lives in
``test_onboarding_readiness_e2e.py``.
"""

from __future__ import annotations

import pytest

from hermes_cli import onboarding_readiness as R
from hermes_cli.onboarding_readiness import (
    OPTIONAL_ITEMS,
    REQUIRED_ITEMS,
    SETUP_SCHEMA,
    ReadinessSignals,
    compute_readiness,
    seen_flag,
)


# ---------------------------------------------------------------------------
# Typed schema: required vs optional
# ---------------------------------------------------------------------------

def test_schema_marks_the_essential_five_as_required() -> None:
    required_keys = {item.key for item in REQUIRED_ITEMS}
    assert required_keys == {
        R.HOME_BOOTSTRAP,
        R.APP_DATASTORE_DSN,
        R.OWNER_IDENTITY,
        R.LLM_PROVIDER_SECRET,
        R.TELEGRAM_CHANNEL,
    }
    assert len(REQUIRED_ITEMS) == 5


def test_optional_items_are_not_required() -> None:
    assert {item.key for item in OPTIONAL_ITEMS} == {
        R.ADDITIONAL_CHANNELS,
        R.MEMORY_PROVIDER,
        R.EXTRA_TOOLS,
    }
    assert all(not item.required for item in OPTIONAL_ITEMS)


def test_every_required_item_has_check_fix_and_rationale() -> None:
    # Each required item must carry a fix action + a one-line rationale, and be
    # evaluatable (the "check"). The check is exercised by compute_readiness.
    signals = ReadinessSignals()
    results = {r.item.key: r for r in compute_readiness(signals).results}
    for item in REQUIRED_ITEMS:
        assert item.fix_command.strip()
        assert item.rationale.strip()
        assert "\n" not in item.rationale  # one line
        assert item.key in results  # has a check


def test_schema_keys_are_unique() -> None:
    keys = [item.key for item in SETUP_SCHEMA]
    assert len(keys) == len(set(keys))


def test_setup_item_as_dict_round_trips_fields() -> None:
    item = REQUIRED_ITEMS[0]
    data = item.as_dict()
    assert data["key"] == item.key
    assert data["required"] is True
    assert set(data) == {
        "key",
        "label",
        "required",
        "rationale",
        "fix_command",
        "contract",
    }


# ---------------------------------------------------------------------------
# Readiness score + prod gate
# ---------------------------------------------------------------------------

def _all_met_signals() -> ReadinessSignals:
    return ReadinessSignals(
        home_bootstrapped=True,
        llm_secret_present=True,
        llm_secret_name="DEEPSEEK_API_KEY",
        app_dsn_present=True,
        owner_enrolled=True,
        owner_detail="owner: u1",
        telegram_token_present=True,
        telegram_channel_bound=True,
    )


def test_empty_signals_score_zero_and_not_prod_ready() -> None:
    readiness = compute_readiness(ReadinessSignals())
    assert readiness.required_met == 0
    assert readiness.required_total == 5
    assert readiness.score == 0.0
    assert readiness.score_pct == 0
    assert readiness.ready_for_prod is False
    assert len(readiness.missing_required()) == 5


def test_all_required_met_is_prod_ready_and_full_score() -> None:
    readiness = compute_readiness(_all_met_signals())
    assert readiness.required_met == 5
    assert readiness.score == 1.0
    assert readiness.score_pct == 100
    assert readiness.ready_for_prod is True
    assert readiness.missing_required() == ()


def test_score_is_required_ratio_not_including_optional() -> None:
    # 3/5 required met, plus ALL optional met — score must reflect required only.
    signals = ReadinessSignals(
        home_bootstrapped=True,
        llm_secret_present=True,
        llm_secret_name="DEEPSEEK_API_KEY",
        app_dsn_present=True,
        owner_enrolled=False,
        telegram_token_present=False,
        additional_channel_present=True,
        memory_provider_present=True,
        extra_tools_present=True,
    )
    readiness = compute_readiness(signals)
    assert readiness.required_met == 3
    assert readiness.score == pytest.approx(3 / 5)
    assert readiness.score_pct == 60
    assert readiness.optional_met == 3
    assert readiness.optional_coverage == 1.0
    # Optional coverage must NOT flip the prod gate.
    assert readiness.ready_for_prod is False


def test_optional_items_never_gate_prod() -> None:
    # All required met, zero optional met → still prod ready.
    readiness = compute_readiness(_all_met_signals())
    assert readiness.optional_met == 0
    assert readiness.ready_for_prod is True


@pytest.mark.parametrize("missing_key", [
    R.HOME_BOOTSTRAP,
    R.APP_DATASTORE_DSN,
    R.OWNER_IDENTITY,
    R.LLM_PROVIDER_SECRET,
    R.TELEGRAM_CHANNEL,
])
def test_any_single_missing_required_blocks_prod(missing_key: str) -> None:
    field_by_key = {
        R.HOME_BOOTSTRAP: {"home_bootstrapped": False},
        R.APP_DATASTORE_DSN: {"app_dsn_present": False},
        R.OWNER_IDENTITY: {"owner_enrolled": False},
        R.LLM_PROVIDER_SECRET: {"llm_secret_present": False},
        R.TELEGRAM_CHANNEL: {"telegram_channel_bound": False},
    }
    kwargs = _all_met_signals().__dict__.copy()
    kwargs.update(field_by_key[missing_key])
    readiness = compute_readiness(ReadinessSignals(**kwargs))
    assert readiness.ready_for_prod is False
    assert missing_key in {r.item.key for r in readiness.missing_required()}


def test_telegram_needs_both_token_and_home_channel() -> None:
    token_only = compute_readiness(
        ReadinessSignals(telegram_token_present=True, telegram_channel_bound=False)
    )
    tg = next(r for r in token_only.results if r.item.key == R.TELEGRAM_CHANNEL)
    assert tg.met is False

    both = compute_readiness(
        ReadinessSignals(telegram_token_present=True, telegram_channel_bound=True)
    )
    tg2 = next(r for r in both.results if r.item.key == R.TELEGRAM_CHANNEL)
    assert tg2.met is True


def test_as_dict_shape_for_dashboard() -> None:
    data = compute_readiness(_all_met_signals()).as_dict()
    assert data["ready_for_prod"] is True
    assert data["score_pct"] == 100
    assert data["required_total"] == 5
    assert isinstance(data["items"], list)
    first = data["items"][0]
    assert {"key", "met", "detail", "required", "rationale", "fix_command"} <= set(first)


# ---------------------------------------------------------------------------
# Secret presence helpers
# ---------------------------------------------------------------------------

def test_secret_present_rejects_blank_and_unresolved_env_refs() -> None:
    assert R._secret_present({"K": "sk-abc"}, "K") is True
    assert R._secret_present({"K": "  "}, "K") is False
    assert R._secret_present({}, "K") is False
    # An unexpanded ${VAR} template is NOT a real secret.
    assert R._secret_present({"K": "${DATABASE_URL}"}, "K") is False


def test_first_present_secret_prefers_declared_order() -> None:
    env = {"OPENAI_API_KEY": "x", "DEEPSEEK_API_KEY": "y"}
    # DEEPSEEK leads the tuple, so it wins even though OPENAI is also present.
    assert R._first_present_secret(env, R.LLM_PROVIDER_SECRET_ENV_VARS) == "DEEPSEEK_API_KEY"


def test_value_present_rejects_unresolved_ref() -> None:
    assert R._value_present("postgresql://u@h/db") is True
    assert R._value_present("${DATABASE_URL}") is False
    assert R._value_present("") is False


# ---------------------------------------------------------------------------
# onboarding.seen flag namespacing (resumability)
# ---------------------------------------------------------------------------

def test_seen_flag_is_namespaced_per_item() -> None:
    assert seen_flag(R.OWNER_IDENTITY) == "setup_owner_identity_done"
    # No collision with agent.onboarding first-touch hint flags.
    from agent import onboarding as ao

    hint_flags = {
        ao.BUSY_INPUT_FLAG,
        ao.TOOL_PROGRESS_FLAG,
        ao.OPENCLAW_RESIDUE_FLAG,
        ao.PROFILE_BUILD_FLAG,
    }
    setup_flags = {seen_flag(i.key) for i in SETUP_SCHEMA}
    assert setup_flags.isdisjoint(hint_flags)
