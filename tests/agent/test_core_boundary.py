"""Unit tests for the Core/Customizable boundary + guard (C7, FG-14).

Covers manifest resolution, escape-safe path classification, the fail-closed
behaviour when the manifest is unreadable, the no-bypass guarantee, and the
C5/C8 audit emission — all against the real ``agent.core_boundary`` module.
"""

import json
import os
from pathlib import Path

import pytest

from agent import core_boundary as cb


# ---------------------------------------------------------------------------
# Fixtures — a self-contained temp "repo" so tests never touch the real tree.
# ---------------------------------------------------------------------------

MANIFEST_TEXT = """\
core_globs:
  - run_agent.py
  - model_tools.py
  - gateway/**
  - hermes_cli/changes.py
  - agent/core_boundary.py
  - core_manifest.yaml
notes: test manifest
"""


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch):
    """A temp repo root with a manifest + a few Core and Customizable files."""
    root = tmp_path / "repo"
    (root / "gateway").mkdir(parents=True)
    (root / "hermes_cli").mkdir(parents=True)
    (root / "agent").mkdir(parents=True)
    (root / "skills" / "demo").mkdir(parents=True)
    (root / "plugins" / "demo").mkdir(parents=True)

    (root / "core_manifest.yaml").write_text(MANIFEST_TEXT, encoding="utf-8")
    (root / "run_agent.py").write_text("# core\n", encoding="utf-8")
    (root / "gateway" / "run.py").write_text("# core gw\n", encoding="utf-8")
    (root / "skills" / "demo" / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    (root / "config.yaml").write_text("k: v\n", encoding="utf-8")

    monkeypatch.setattr(cb, "get_core_root", lambda: root)
    # Isolate the audit log under a temp HERMES_HOME.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    return root


# ---------------------------------------------------------------------------
# Manifest resolution
# ---------------------------------------------------------------------------

class TestManifestResolution:
    def test_reads_globs_from_manifest(self, fake_repo):
        globs = cb.load_core_globs()
        assert "run_agent.py" in globs
        assert "gateway/**" in globs
        assert "core_manifest.yaml" in globs

    def test_shipped_manifest_protects_real_core_files(self):
        # Against the REAL repo (no monkeypatch): the committed manifest must
        # classify the load-bearing entry points as Core. Pure classification,
        # no writes — safe to run against the live tree.
        root = cb.get_core_root()
        assert cb.is_core_path(root / "run_agent.py")
        assert cb.is_core_path(root / "model_tools.py")
        assert cb.is_core_path(root / "toolsets.py")
        assert cb.is_core_path(root / "cli.py")
        assert cb.is_core_path(root / "hermes_state.py")
        assert cb.is_core_path(root / "gateway" / "run.py")
        assert cb.is_core_path(root / "hermes_cli" / "changes.py")
        # Self-protecting: the guard + manifest themselves are Core.
        assert cb.is_core_path(root / "agent" / "core_boundary.py")
        assert cb.is_core_path(root / "core_manifest.yaml")

    def test_shipped_customizable_areas_not_core(self):
        root = cb.get_core_root()
        assert not cb.is_core_path(root / "skills" / "anything" / "SKILL.md")
        assert not cb.is_core_path(root / "plugins" / "anything" / "__init__.py")
        assert not cb.is_core_path(root / "config.yaml")


# ---------------------------------------------------------------------------
# Classification (Core vs Customizable)
# ---------------------------------------------------------------------------

class TestClassification:
    def test_core_files(self, fake_repo):
        assert cb.classify_core_path(fake_repo / "run_agent.py") == "run_agent.py"
        assert cb.classify_core_path(fake_repo / "gateway" / "run.py") == "gateway/**"
        assert (
            cb.classify_core_path(fake_repo / "gateway" / "platforms" / "telegram.py")
            == "gateway/**"
        )

    def test_customizable_files(self, fake_repo):
        assert cb.classify_core_path(fake_repo / "skills" / "demo" / "SKILL.md") is None
        assert cb.classify_core_path(fake_repo / "plugins" / "demo" / "x.py") is None
        assert cb.classify_core_path(fake_repo / "config.yaml") is None

    def test_relative_path_resolves_against_cwd(self, fake_repo, monkeypatch):
        monkeypatch.chdir(fake_repo)
        assert cb.classify_core_path("run_agent.py") == "run_agent.py"
        assert cb.classify_core_path("skills/demo/SKILL.md") is None

    def test_target_outside_repo_is_customizable(self, fake_repo, tmp_path):
        outside = tmp_path / "elsewhere" / "run_agent.py"
        assert cb.classify_core_path(outside) is None

    def test_repo_root_itself_not_core(self, fake_repo):
        assert cb.classify_core_path(fake_repo) is None


# ---------------------------------------------------------------------------
# Escape-safety: .., symlinks, absolute paths
# ---------------------------------------------------------------------------

class TestEscapeDefenses:
    def test_dotdot_traversal_into_core_is_caught(self, fake_repo):
        sneaky = fake_repo / "skills" / ".." / ".." / "repo" / "run_agent.py"
        # normpath-only would keep it as skills/../.. — realpath resolves it.
        assert cb.classify_core_path(sneaky) == "run_agent.py"

    def test_absolute_path_into_core_is_caught(self, fake_repo):
        assert cb.classify_core_path(str(fake_repo / "run_agent.py")) == "run_agent.py"

    def test_symlink_pointing_at_core_is_caught(self, fake_repo):
        link = fake_repo / "skills" / "demo" / "sneaky_link.py"
        os.symlink(fake_repo / "run_agent.py", link)
        # The link lives in a Customizable dir but RESOLVES to Core → denied.
        assert cb.classify_core_path(link) == "run_agent.py"

    def test_symlinked_parent_dir_into_core_is_caught(self, fake_repo):
        # A symlinked directory that lands inside gateway/.
        link_dir = fake_repo / "plugins" / "gwlink"
        os.symlink(fake_repo / "gateway", link_dir)
        assert cb.classify_core_path(link_dir / "run.py") == "gateway/**"


# ---------------------------------------------------------------------------
# Fail-closed behaviour
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_missing_manifest_uses_fallback(self, fake_repo):
        (fake_repo / "core_manifest.yaml").unlink()
        globs = cb.load_core_globs()
        assert globs == cb._FALLBACK_CORE_GLOBS
        # Core stays protected even with no manifest on disk.
        assert cb.is_core_path(fake_repo / "run_agent.py")
        assert cb.is_core_path(fake_repo / "gateway" / "run.py")

    def test_unparseable_manifest_uses_fallback(self, fake_repo):
        (fake_repo / "core_manifest.yaml").write_text(": : not : yaml :\n[", encoding="utf-8")
        assert cb.load_core_globs() == cb._FALLBACK_CORE_GLOBS
        assert cb.is_core_path(fake_repo / "run_agent.py")

    def test_manifest_without_core_globs_uses_fallback(self, fake_repo):
        (fake_repo / "core_manifest.yaml").write_text("notes: nothing here\n", encoding="utf-8")
        assert cb.load_core_globs() == cb._FALLBACK_CORE_GLOBS

    def test_empty_core_globs_list_uses_fallback(self, fake_repo):
        (fake_repo / "core_manifest.yaml").write_text("core_globs: []\n", encoding="utf-8")
        assert cb.load_core_globs() == cb._FALLBACK_CORE_GLOBS

    def test_fallback_matches_shipped_manifest(self):
        # The baked-in safety net must not drift from the committed manifest:
        # every fallback glob must be present in the real manifest.
        root = cb.get_core_root()
        import yaml
        manifest = yaml.safe_load((root / "core_manifest.yaml").read_text())
        manifest_globs = set(manifest["core_globs"])
        assert set(cb._FALLBACK_CORE_GLOBS) == manifest_globs


# ---------------------------------------------------------------------------
# No-bypass guarantee — nothing disables the guard.
# ---------------------------------------------------------------------------

class TestNoBypass:
    def test_no_env_var_disables_the_guard(self, fake_repo, monkeypatch):
        # Throw plausible "disable" switches at it — none may take effect.
        for var in (
            "HERMES_CORE_GUARD",
            "HERMES_DISABLE_CORE_GUARD",
            "HERMES_CORE_GUARD_DISABLE",
            "HERMES_ALLOW_CORE_WRITE",
            "HERMES_YOLO",
            "YOLO",
        ):
            monkeypatch.setenv(var, "1")
        monkeypatch.setenv("HERMES_CORE_GUARD", "off")
        assert cb.check_core_write(fake_repo / "run_agent.py") is not None

    def test_write_safe_root_including_core_does_not_allow_core_write(
        self, fake_repo, monkeypatch
    ):
        # HERMES_WRITE_SAFE_ROOT is the credential-denylist sandbox knob; even
        # pointing it at the repo cannot re-open Core to the agent.
        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(fake_repo))
        assert cb.check_core_write(fake_repo / "run_agent.py") is not None

    def test_guard_module_exposes_no_disable_api(self):
        names = dir(cb)
        assert not any("disable" in n.lower() for n in names)
        assert not any("bypass" in n.lower() for n in names)

    def test_check_returns_none_only_for_customizable(self, fake_repo):
        assert cb.check_core_write(fake_repo / "skills" / "demo" / "SKILL.md") is None
        assert cb.check_core_write(fake_repo / "run_agent.py") is not None


# ---------------------------------------------------------------------------
# Audit emission (C5 change-audit + C8 core_denied trace)
# ---------------------------------------------------------------------------

class TestAudit:
    def test_denied_write_writes_local_jsonl(self, fake_repo):
        cb.check_core_write(fake_repo / "run_agent.py", op="write")
        log = cb.core_audit_log_path()
        assert log.exists()
        rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        assert len(rows) == 1
        row = rows[0]
        assert row["target_kind"] == "code"
        assert row["reversible"] is False
        assert row["kind"] == "core_denied"          # C8 trace kind
        assert row["op"]["kind"] == "core_write_denied"
        assert row["op"]["matched_glob"] == "run_agent.py"
        assert row["op"]["op"] == "write"
        assert row["op"]["path"].endswith("run_agent.py")

    def test_allowed_write_writes_no_audit(self, fake_repo):
        cb.check_core_write(fake_repo / "skills" / "demo" / "SKILL.md")
        assert not cb.core_audit_log_path().exists()

    def test_registered_sinks_receive_event(self, fake_repo):
        change_events, trace_events = [], []
        cb.register_change_recorder(change_events.append)
        cb.register_trace_emitter(trace_events.append)
        try:
            cb.check_core_write(fake_repo / "run_agent.py", op="patch")
        finally:
            cb.register_change_recorder(None)
            cb.register_trace_emitter(None)
        assert len(change_events) == 1
        assert len(trace_events) == 1
        assert trace_events[0]["kind"] == "core_denied"
        assert change_events[0]["op"]["op"] == "patch"

    def test_failing_sink_does_not_raise(self, fake_repo):
        def boom(_event):
            raise RuntimeError("sink down")

        cb.register_change_recorder(boom)
        cb.register_trace_emitter(boom)
        try:
            # Must not propagate — the audit is best-effort, the guard is not.
            assert cb.check_core_write(fake_repo / "run_agent.py") is not None
        finally:
            cb.register_change_recorder(None)
            cb.register_trace_emitter(None)
        # Local JSONL still written despite the failing sinks.
        assert cb.core_audit_log_path().exists()

    def test_actor_defaults_to_runtime_agent(self, fake_repo):
        event = cb.record_core_denied(
            path=str(fake_repo / "run_agent.py"), matched_glob="run_agent.py", op="write"
        )
        assert event["actor_user_id"] == "runtime-agent"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
