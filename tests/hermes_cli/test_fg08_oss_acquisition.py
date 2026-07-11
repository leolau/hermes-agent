"""Unit tests for the FG-08 OSS acquisition primitives (no data layer).

Behaviour/invariant coverage for the pure, side-effect-light pieces of the
acquisition pipeline — the license allowlist rail, discovery/ranking, vetting,
and the generated ``fastmcp`` wrapper. The real-path registry/provenance/
approval flow (against a throwaway Postgres) lives in
``test_fg08_oss_pipeline_e2e.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from hermes_cli.oss_acquisition import (
    Candidate,
    HostSpec,
    LICENSE_ALLOWLIST,
    ServiceHandle,
    choose_mode,
    discover_candidates,
    fit_score,
    generate_solution_mcp,
    is_license_allowed,
    normalize_license,
    vet_candidate,
)


# ── license allowlist rail (§4.3) ───────────────────────────────────────────

@pytest.mark.parametrize(
    "raw",
    ["MIT", "mit", "MIT License", "Apache-2.0", "apache 2.0", "BSD-3-Clause",
     "BSD", "new BSD", "ISC", "The Unlicense", "0BSD"],
)
def test_permissive_licenses_are_allowed(raw: str) -> None:
    assert is_license_allowed(raw) is True
    assert normalize_license(raw) in LICENSE_ALLOWLIST


@pytest.mark.parametrize(
    "raw",
    ["GPL-3.0", "AGPL-3.0", "LGPL-2.1", "MPL-2.0", "SSPL-1.0",
     "CC-BY-NC-4.0", "proprietary", "", None, "NOASSERTION", "gobbledygook"],
)
def test_copyleft_and_unknown_licenses_fail_closed(raw) -> None:
    assert is_license_allowed(raw) is False


# ── discovery / ranking / mode choice ───────────────────────────────────────

def _fake_search(goal: str, *, limit: int = 5):
    return [
        Candidate("markitdown", "https://github.com/x/markitdown", "MIT", 5000,
                  description="convert documents to markdown"),
        Candidate("obscure", "https://github.com/x/obscure", "MIT", 3,
                  description="convert documents to markdown"),
        Candidate("copyleft-doc", "https://github.com/x/cl", "GPL-3.0", 9000,
                  description="convert documents to markdown pdf"),
        Candidate("unrelated", "https://github.com/x/game", "MIT", 100,
                  description="a snake game"),
    ][:limit]


def test_fit_score_rewards_keyword_overlap_and_activity() -> None:
    goal = "convert documents to markdown"
    hits = _fake_search(goal)
    popular = fit_score(hits[0], goal)   # full overlap + many stars
    faint = fit_score(hits[3], goal)     # no overlap
    assert popular > faint
    assert 0.0 <= faint <= popular <= 1.0


def test_discover_ranks_by_fit_and_can_filter_licenses() -> None:
    goal = "convert documents to markdown"
    ranked = discover_candidates(goal, _fake_search, limit=5)
    # Ranking is fit-only (license-agnostic): an on-topic repo beats the
    # unrelated snake game regardless of stars.
    assert ranked.index(
        next(c for c in ranked if c.name == "markitdown")
    ) < ranked.index(next(c for c in ranked if c.name == "unrelated"))

    # allowed_only drops the copyleft candidate entirely; the popular on-topic
    # MIT repo then leads.
    allowed = discover_candidates(goal, _fake_search, limit=5, allowed_only=True)
    assert all(c.license_ok for c in allowed)
    assert "copyleft-doc" not in {c.name for c in allowed}
    assert allowed[0].name == "markitdown"


def test_choose_mode_prefers_remote_unless_forced_or_no_oss() -> None:
    permissive = [Candidate("a", "u", "MIT", 10)]
    copyleft = [Candidate("a", "u", "GPL-3.0", 10)]
    assert choose_mode(explicit_in_house=False, candidates=permissive) == "remote"
    assert choose_mode(explicit_in_house=True, candidates=permissive) == "in_house"
    # No permissively-licensed OSS -> fall back to an in-house rebuild.
    assert choose_mode(explicit_in_house=False, candidates=copyleft) == "in_house"
    assert choose_mode(explicit_in_house=False, candidates=[]) == "in_house"


# ── vetting (stage 2) ────────────────────────────────────────────────────────

def test_vet_passes_clean_permissive_candidate() -> None:
    report = vet_candidate(Candidate("ok", "https://github.com/x/ok", "MIT", 10))
    assert report.passed is True
    assert report.findings == ()


def test_vet_rejects_disallowed_license() -> None:
    report = vet_candidate(Candidate("bad", "https://github.com/x/bad", "GPL-3.0"))
    assert report.license_ok is False
    assert report.passed is False
    assert any("allowlist" in f for f in report.findings)


def test_vet_surfaces_scan_and_smoke_failures() -> None:
    candidate = Candidate("mit", "https://github.com/x/mit", "MIT", 10)
    report = vet_candidate(
        candidate,
        supply_chain_scan=lambda _c: ["vulnerable dep foo@1.0"],
        secret_scan=lambda _c: ["hardcoded AWS key"],
        smoke_test=lambda _c: False,
    )
    assert report.passed is False
    joined = "\n".join(report.findings)
    assert "supply-chain" in joined and "secret-scan" in joined
    assert "smoke" in joined


# ── generated fastmcp wrapper (stage 5) ──────────────────────────────────────

def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"gen_{path.stem}_{id(path)}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generate_solution_mcp_writes_wrapper_outside_core(tmp_path: Path) -> None:
    root = tmp_path / "internal-solutions"
    solution = generate_solution_mcp(
        "markitdown",
        root,
        repo_url="https://github.com/microsoft/markitdown",
        commit="abc123def456",
        license="MIT",
        host="ai-prentice-2",
        upstream_url="",
    )
    # Wrapper lives under the provided (out-of-core) root, not the repo tree.
    assert solution.root == root / "markitdown"
    assert solution.server_path.is_file()
    assert "solution.json" in solution.files

    # The provenance sidecar pins repo/commit/license/host.
    sidecar = json.loads((solution.root / "solution.json").read_text())
    assert sidecar["commit"] == "abc123def456"
    assert sidecar["license"] == "MIT"
    assert sidecar["host"] == "ai-prentice-2"

    # stdio transport shape the FG-11 registry consumes; no HERMES_* env vars.
    transport = solution.mcp_transport()
    assert transport["type"] == "stdio"
    assert transport["args"] == [str(solution.server_path)]
    assert "env" not in transport


def test_generated_wrapper_is_a_reachable_fastmcp_server(tmp_path: Path) -> None:
    solution = generate_solution_mcp(
        "docwrap",
        tmp_path,
        repo_url="https://github.com/x/doc",
        commit="deadbeefcafe",
        license="Apache-2.0",
        host="ai-prentice-9",
        upstream_url="",
    )
    module = _load_module(solution.server_path)

    # The generated fastmcp server exposes the two read-only tools.
    tool_names = {t.name for t in asyncio.run(module.mcp.list_tools())}
    assert {"provenance", "health"} <= tool_names

    # The provenance tool returns the pinned metadata.
    prov = module.provenance()
    assert prov["commit"] == "deadbeefcafe"
    assert prov["license"] == "Apache-2.0"
    assert prov["repo_url"] == "https://github.com/x/doc"

    # health() degrades gracefully with no upstream configured.
    assert module.health()["reachable"] is False


# ── host spec rails ──────────────────────────────────────────────────────────

def test_host_spec_defaults_are_locked_down() -> None:
    spec = HostSpec(host="ai-prentice-2")
    assert spec.non_root is True
    assert spec.network_restricted is True
    assert spec.bind == "127.0.0.1"


def test_service_handle_gets_stable_id() -> None:
    handle = ServiceHandle(name="x", remote_path="/opt/x", base_url="http://127.0.0.1:9")
    assert handle.handle_id.startswith("svc_")
