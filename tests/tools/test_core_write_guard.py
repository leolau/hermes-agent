"""Security + real-path E2E for the Core write-guard at the file chokepoint (C7, FG-14).

These exercise the *real* ``ShellFileOperations`` backend (the exact object the
runtime agent's file tools drive, executing real shell) against an on-disk temp
"repo" and a temp ``HERMES_HOME`` — no mocks. They prove:

* a runtime-agent write / patch / delete / move to a Core path is **refused**
  and the Core bytes are left untouched;
* the denial is **audited** (a C5-shaped change row + a C8 ``core_denied``
  trace event);
* a write to a Customizable path **succeeds** and mutates the file;
* the guard cannot be bypassed via ``HERMES_WRITE_SAFE_ROOT``.
"""

import json
from pathlib import Path

import pytest

from agent import core_boundary as cb


MANIFEST_TEXT = """\
core_globs:
  - run_agent.py
  - gateway/**
  - agent/core_boundary.py
  - core_manifest.yaml
notes: e2e test manifest
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch):
    """On-disk temp repo (Core + Customizable files) with the guard pointed at it."""
    root = tmp_path / "repo"
    (root / "gateway").mkdir(parents=True)
    (root / "agent").mkdir(parents=True)
    (root / "skills" / "demo").mkdir(parents=True)
    (root / "core_manifest.yaml").write_text(MANIFEST_TEXT, encoding="utf-8")
    (root / "run_agent.py").write_text("CORE_ORIGINAL = 1\n", encoding="utf-8")
    (root / "gateway" / "run.py").write_text("GW_ORIGINAL = 1\n", encoding="utf-8")
    (root / "skills" / "demo" / "SKILL.md").write_text("original skill\n", encoding="utf-8")

    monkeypatch.setattr(cb, "get_core_root", lambda: root)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    return root


@pytest.fixture
def ops(repo: Path):
    from tools.environments.local import LocalEnvironment
    from tools.file_operations import ShellFileOperations
    env = LocalEnvironment(cwd=str(repo))
    return ShellFileOperations(env, cwd=str(repo))


def _audit_rows() -> list[dict]:
    log = cb.core_audit_log_path()
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text().splitlines() if x.strip()]


class TestSecurityWriteDenied:
    def test_write_to_core_is_refused_and_bytes_unchanged(self, ops, repo):
        target = repo / "run_agent.py"
        res = ops.write_file(str(target), "MALICIOUS = 1\n")
        assert res.error is not None
        assert "Core" in res.error
        # The file was never touched.
        assert target.read_text() == "CORE_ORIGINAL = 1\n"

    def test_denied_write_is_audited_c5_and_c8(self, ops, repo):
        trace = []
        cb.register_trace_emitter(trace.append)
        try:
            ops.write_file(str(repo / "run_agent.py"), "MALICIOUS = 1\n")
        finally:
            cb.register_trace_emitter(None)
        rows = _audit_rows()
        assert len(rows) == 1
        assert rows[0]["target_kind"] == "code"          # C5 shape
        assert rows[0]["reversible"] is False
        assert rows[0]["kind"] == "core_denied"           # C8 trace kind
        assert rows[0]["op"]["matched_glob"] == "run_agent.py"
        assert len(trace) == 1 and trace[0]["kind"] == "core_denied"

    def test_patch_to_core_is_refused(self, ops, repo):
        target = repo / "run_agent.py"
        res = ops.patch_replace(str(target), "CORE_ORIGINAL = 1", "CORE_ORIGINAL = 999")
        assert res.success is False
        assert res.error is not None and "Core" in res.error
        assert target.read_text() == "CORE_ORIGINAL = 1\n"

    def test_delete_of_core_is_refused(self, ops, repo):
        target = repo / "gateway" / "run.py"
        res = ops.delete_file(str(target))
        assert res.error is not None and "Core" in res.error
        assert target.exists()

    def test_move_onto_core_is_refused(self, ops, repo):
        src = repo / "skills" / "demo" / "SKILL.md"
        res = ops.move_file(str(src), str(repo / "run_agent.py"))
        assert res.error is not None and "Core" in res.error
        assert (repo / "run_agent.py").read_text() == "CORE_ORIGINAL = 1\n"
        assert src.exists()  # source not moved either

    def test_move_of_core_source_is_refused(self, ops, repo):
        src = repo / "gateway" / "run.py"
        res = ops.move_file(str(src), str(repo / "skills" / "demo" / "stolen.py"))
        assert res.error is not None and "Core" in res.error
        assert src.exists()

    def test_nested_gateway_write_refused(self, ops, repo):
        target = repo / "gateway" / "platforms" / "telegram.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("GW = 1\n")
        res = ops.write_file(str(target), "HACKED = 1\n")
        assert res.error is not None and "Core" in res.error
        assert target.read_text() == "GW = 1\n"


class TestCustomizableWriteAllowed:
    def test_write_to_skill_succeeds_and_mutates(self, ops, repo):
        target = repo / "skills" / "demo" / "SKILL.md"
        res = ops.write_file(str(target), "improved skill\n")
        assert res.error is None, res.error
        assert target.read_text() == "improved skill\n"
        # No Core-denial audit for an allowed write.
        assert _audit_rows() == []

    def test_create_new_customizable_file_succeeds(self, ops, repo):
        target = repo / "skills" / "demo" / "new_tool.md"
        res = ops.write_file(str(target), "brand new\n")
        assert res.error is None, res.error
        assert target.read_text() == "brand new\n"

    def test_patch_customizable_succeeds(self, ops, repo):
        target = repo / "skills" / "demo" / "SKILL.md"
        res = ops.patch_replace(str(target), "original skill", "patched skill")
        assert res.success, res.error
        assert target.read_text() == "patched skill\n"


class TestNoBypassAtWritePath:
    def test_safe_root_pointing_at_repo_does_not_reopen_core(self, ops, repo, monkeypatch):
        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(repo))
        res = ops.write_file(str(repo / "run_agent.py"), "X = 1\n")
        assert res.error is not None and "Core" in res.error
        assert (repo / "run_agent.py").read_text() == "CORE_ORIGINAL = 1\n"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
