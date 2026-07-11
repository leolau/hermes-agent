"""Unit tests for FG-12 change management: the C6 consent policy, the op
builders, and the config/code inverse-op + checkpoint-restore engines.

These exercise the real paths that do not need Postgres (config.yaml round-trip
against a temp HERMES_HOME, and checkpoint restore against a real throwaway git
shadow store). The DB-backed C5 log + C2 scoping live in
``test_changes_e2e.py``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from hermes_cli.changes import (
    NotUndoable,
    _apply_code_op,
    _apply_config_op,
    code_op,
    config_op,
    data_op,
)
from hermes_cli.consent import (
    ConsentPolicy,
    ConsentDecision,
    evaluate_approval,
    load_consent_policy,
)


# ---------------------------------------------------------------------------
# C6 consent policy
# ---------------------------------------------------------------------------


def test_quiet_hours_same_day_and_wrapping_midnight() -> None:
    day = ConsentPolicy(quiet_hours_start=9, quiet_hours_end=17)
    assert day.within_quiet_hours(datetime(2026, 1, 1, 12))
    assert not day.within_quiet_hours(datetime(2026, 1, 1, 8))
    assert not day.within_quiet_hours(datetime(2026, 1, 1, 17))

    night = ConsentPolicy(quiet_hours_start=22, quiet_hours_end=7)
    assert night.within_quiet_hours(datetime(2026, 1, 1, 23))
    assert night.within_quiet_hours(datetime(2026, 1, 1, 3))
    assert not night.within_quiet_hours(datetime(2026, 1, 1, 12))

    # No window configured → never quiet.
    assert not ConsentPolicy().within_quiet_hours(datetime(2026, 1, 1, 3))


def test_rate_limit_ceiling() -> None:
    policy = ConsentPolicy(rate_limit_max=3)
    assert not policy.is_rate_limited(2)
    assert policy.is_rate_limited(3)
    assert policy.is_rate_limited(9)
    # No limit configured → never limited.
    assert not ConsentPolicy().is_rate_limited(1000)


def test_load_consent_policy_from_config_dict() -> None:
    cfg = {
        "change_management": {
            "auto_approve_reversible": True,
            "quiet_hours": {"start": 22, "end": 7},
            "rate_limit": {"max": 5, "window_seconds": 600},
        }
    }
    policy = load_consent_policy(cfg)
    assert policy.auto_approve_reversible is True
    assert policy.quiet_hours_start == 22 and policy.quiet_hours_end == 7
    assert policy.rate_limit_max == 5 and policy.rate_limit_window_seconds == 600


def test_load_consent_policy_defaults_are_safe() -> None:
    policy = load_consent_policy({})
    assert policy.auto_approve_reversible is False
    assert policy.quiet_hours_start is None
    assert policy.rate_limit_max is None
    assert policy.rate_limit_window_seconds == 3600
    # Malformed values fall back rather than raising.
    bad = load_consent_policy({"change_management": {"quiet_hours": {"start": 99}}})
    assert bad.quiet_hours_start is None


def _record_prompt(choice: str) -> tuple[list[tuple[str, str]], object]:
    calls: list[tuple[str, str]] = []

    def cb(command: str, description: str, **_: object) -> str:
        calls.append((command, description))
        return choice

    return calls, cb


def test_irreversible_always_prompts_even_with_consent() -> None:
    # Consent ON, outside quiet-hours, under rate-limit — a reversible change
    # would auto-approve, but an irreversible one must still be prompted.
    policy = ConsentPolicy(auto_approve_reversible=True)
    calls, cb = _record_prompt("once")
    decision = evaluate_approval(
        policy,
        reversible=False,
        command="hermes changes record data:mint",
        description="ERC-721 mint",
        now=datetime(2026, 1, 1, 12),
        approval_callback=cb,
    )
    assert decision == ConsentDecision(approved=True, mode="prompted", reason="irreversible")
    assert calls  # the user was actually asked


def test_irreversible_denied_is_refused() -> None:
    calls, cb = _record_prompt("deny")
    decision = evaluate_approval(
        ConsentPolicy(auto_approve_reversible=True),
        reversible=False,
        command="c",
        description="d",
        approval_callback=cb,
    )
    assert decision.approved is False and decision.mode == "denied"


def test_reversible_auto_approves_only_with_consent() -> None:
    calls, cb = _record_prompt("once")
    # Consent off → prompt.
    d1 = evaluate_approval(
        ConsentPolicy(auto_approve_reversible=False),
        reversible=True, command="c", description="d", approval_callback=cb,
    )
    assert d1.mode == "prompted" and d1.reason == "consent_off"
    assert len(calls) == 1

    # Consent on, quiet hours + rate-limit clear → auto (no prompt).
    d2 = evaluate_approval(
        ConsentPolicy(auto_approve_reversible=True),
        reversible=True, command="c", description="d",
        now=datetime(2026, 1, 1, 12), approval_callback=cb,
    )
    assert d2 == ConsentDecision(approved=True, mode="auto", reason="consent")
    assert len(calls) == 1  # not prompted again


def test_reversible_prompts_during_quiet_hours_and_when_rate_limited() -> None:
    policy = ConsentPolicy(auto_approve_reversible=True, quiet_hours_start=22,
                           quiet_hours_end=7, rate_limit_max=2)
    _, cb = _record_prompt("session")
    quiet = evaluate_approval(policy, reversible=True, command="c", description="d",
                              now=datetime(2026, 1, 1, 23), approval_callback=cb)
    assert quiet.mode == "prompted" and quiet.reason == "quiet_hours"

    limited = evaluate_approval(policy, reversible=True, command="c", description="d",
                                now=datetime(2026, 1, 1, 12), recent_auto_approvals=2,
                                approval_callback=cb)
    assert limited.mode == "prompted" and limited.reason == "rate_limited"


# ---------------------------------------------------------------------------
# Op builders
# ---------------------------------------------------------------------------


def test_op_builders_produce_symmetric_pairs() -> None:
    fwd, inv = config_op("a.b", before="old", after="new")
    assert fwd == {"kind": "config", "path": "a.b", "present": True, "value": "new"}
    assert inv == {"kind": "config", "path": "a.b", "present": True, "value": "old"}

    fwd, inv = data_op("t", {"id": 1}, before=None, after={"id": 1, "v": "x"})
    assert fwd["state"] == {"id": 1, "v": "x"} and inv["state"] is None

    fwd, inv = code_op("/w", commit_before="aaa", commit_after="bbb")
    assert fwd["commit"] == "bbb" and inv["commit"] == "aaa"


def test_apply_config_op_unknown_kind_is_refused() -> None:
    with pytest.raises(NotUndoable):
        _apply_config_op({"kind": "config"})  # no path


# ---------------------------------------------------------------------------
# Config inverse-op round-trip (real config.yaml under a temp HERMES_HOME)
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_config_op_round_trip(temp_home: Path) -> None:
    from hermes_cli.config import read_raw_config, save_config

    save_config({"agent": {"reasoning_effort": "low"}}, strip_defaults=False)

    fwd, inv = config_op("agent.reasoning_effort", before="low", after="high")

    _apply_config_op(fwd)
    assert read_raw_config()["agent"]["reasoning_effort"] == "high"

    _apply_config_op(inv)
    assert read_raw_config()["agent"]["reasoning_effort"] == "low"


def test_config_op_remove_restores_absence(temp_home: Path) -> None:
    from hermes_cli.config import read_raw_config, save_config

    save_config({"agent": {}}, strip_defaults=False)
    # Add a key (inverse marks it absent), then undo the add.
    fwd, inv = config_op("feature.flag", before=None, after=True,
                         before_present=False, after_present=True)
    _apply_config_op(fwd)
    assert read_raw_config()["feature"]["flag"] is True
    _apply_config_op(inv)
    assert "flag" not in read_raw_config().get("feature", {})


# ---------------------------------------------------------------------------
# Code undo/redo via the real checkpoint restore engine
# ---------------------------------------------------------------------------


@pytest.fixture()
def checkpoint_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git is required for checkpoint restore")
    base = tmp_path / "checkpoints"
    monkeypatch.setattr("tools.checkpoint_manager.CHECKPOINT_BASE", base)
    work = tmp_path / "project"
    work.mkdir()
    return work


def test_code_op_undo_and_redo_via_checkpoint(checkpoint_env: Path) -> None:
    from tools.checkpoint_manager import CheckpointManager

    target = checkpoint_env / "main.py"
    target.write_text("print('v1')\n")

    mgr = CheckpointManager(enabled=True, max_snapshots=50)
    assert mgr.ensure_checkpoint(str(checkpoint_env), "v1")

    target.write_text("print('v2')\n")
    mgr.new_turn()
    assert mgr.ensure_checkpoint(str(checkpoint_env), "v2")

    checkpoints = mgr.list_checkpoints(str(checkpoint_env))
    assert len(checkpoints) >= 2
    commit_after, commit_before = checkpoints[0]["hash"], checkpoints[1]["hash"]

    fwd, inv = code_op(str(checkpoint_env), commit_before=commit_before,
                       commit_after=commit_after, file_path="main.py")

    # Undo → back to v1.
    detail = _apply_code_op(inv)
    assert "restored" in detail
    assert target.read_text() == "print('v1')\n"

    # Redo → forward to v2.
    _apply_code_op(fwd)
    assert target.read_text() == "print('v2')\n"


def test_code_op_bad_commit_refused(checkpoint_env: Path) -> None:
    with pytest.raises(NotUndoable):
        _apply_code_op({"kind": "code", "working_dir": str(checkpoint_env),
                        "commit": "not-a-hash"})
