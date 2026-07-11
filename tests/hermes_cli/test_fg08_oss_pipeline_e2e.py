"""Real-path E2E for the FG-08 OSS acquisition pipeline (throwaway Postgres).

Exercises the whole acquisition stack with real imports and a real data layer
(contract C3 datastore routing, C1/C2 principals + scoping, C5/C6 change +
approval, the FG-07 tool registry, the FG-11 endpoint registry, and the FG-08
provenance registry) — no mocks of the data layer. The only thing stubbed is
the *remote host*: a recording :class:`_FakeHostRunner` stands in for the
different machine so the provenance/registration/wrapping flow runs end to end
without deploying anywhere.

Coverage:

* **remote pipeline (§4.3):** discover → approve(#1) → vet → adapt(clone off-box,
  commit-pinned) → run(non-root, network-restricted) → expose-MCP → approve(#2)
  → register FG-07 ``remote`` tool + FG-11 endpoint + provenance; the generated
  fastmcp wrapper is a reachable MCP server and lives OUTSIDE the core tree;
* **hard rails:** a disallowed license is rejected and registers nothing; an
  unapproved evaluate/apply gate blocks and registers nothing; a missing commit
  pin and a root/unrestricted host are refused;
* **in-house path:** delegates to the FG-07 scaffolder, registers an
  ``in_house`` tool + endpoint + provenance, and the generated Node MCP server
  completes a real ``initialize`` handshake;
* **negative access (C2):** a member's private remote system is invisible to
  another member; the owner sees it;
* **mode isolation (C3):** a dev acquisition never surfaces from a prod store.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest

from hermes_cli.access import Principal
from hermes_cli.datastore import get_store, initialize_supabase_app
from hermes_cli.oss_acquisition import (
    AcquisitionError,
    Candidate,
    HostSpec,
    LicenseNotAllowedError,
    OSSAcquisition,
    ServiceHandle,
)

_PGVECTOR_IMAGE = (
    "pgvector/pgvector@sha256:"
    "1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)


async def _probe_postgres(dsn: str) -> None:
    connection = await asyncpg.connect(dsn, ssl=False)
    await connection.close()


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the FG-08 acquisition E2E test")
    daemon = subprocess.run(
        ["docker", "info"], check=False, capture_output=True, text=True
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable for the FG-08 acquisition E2E test")

    subprocess.run(["docker", "pull", _PGVECTOR_IMAGE], check=True, capture_output=True)
    container = f"hermes-fg08-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--env", "POSTGRES_PASSWORD=hermes-test",
            "--env", "POSTGRES_DB=hermes_test",
            "--publish", "127.0.0.1::5432",
            _PGVECTOR_IMAGE,
        ],
        check=True, capture_output=True, text=True,
    )
    try:
        port_result = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            check=True, capture_output=True, text=True,
        )
        port = port_result.stdout.strip().rsplit(":", 1)[1]
        dsn = f"postgresql://postgres:hermes-test@127.0.0.1:{port}/hermes_test"
        for _ in range(60):
            try:
                asyncio.run(_probe_postgres(dsn))
                break
            except (OSError, asyncpg.PostgresError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Throwaway Postgres did not become ready")
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False, capture_output=True,
        )


def _config(dsn: str) -> dict:
    return {"datastore": {"supabase_app": {"dsn": dsn}}}


async def _reset(dsn: str) -> None:
    conn = await asyncpg.connect(dsn, ssl=False)
    try:
        await conn.execute(
            "DROP SCHEMA IF EXISTS app_dev CASCADE;"
            "DROP SCHEMA IF EXISTS app_prod CASCADE;"
        )
        await initialize_supabase_app(conn)
    finally:
        await conn.close()


_OWNER = Principal(user_id="root", display="Root", role="owner")
_ALICE = Principal(user_id="alice", display="Alice", role="member")
_BOB = Principal(user_id="bob", display="Bob", role="member")
_VIEWER = Principal(user_id="vic", display="Vic", role="viewer")

_APPROVE = lambda *_a, **_k: "once"  # noqa: E731 - test approval callback
_DENY = lambda *_a, **_k: "deny"  # noqa: E731 - test denial callback


class _FakeHostRunner:
    """Records the §4.3 clone/run/health/stop calls (no real deployment)."""

    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.calls: list[tuple] = []

    def clone(self, repo_url: str, commit: str, *, dest: str) -> str:
        self.calls.append(("clone", repo_url, commit, dest))
        return dest

    def run_service(
        self, remote_path, *, name, non_root, network_restricted, bind
    ) -> ServiceHandle:
        self.calls.append(("run", remote_path, name, non_root, network_restricted, bind))
        return ServiceHandle(name=name, remote_path=remote_path, base_url="")

    def health_check(self, handle: ServiceHandle) -> bool:
        self.calls.append(("health", handle.name))
        return self.healthy

    def stop(self, handle: ServiceHandle) -> None:
        self.calls.append(("stop", handle.name))


def _acquisition(dsn: str, mode: str) -> OSSAcquisition:
    cfg = _config(dsn)
    return OSSAcquisition(
        get_store("supabase-app", mode, config=cfg),
        prod_store=get_store("supabase-app", "prod", config=cfg),
    )


def _markitdown(name: str = "markitdown", license: str = "MIT") -> Candidate:
    return Candidate(
        name=name,
        repo_url="https://github.com/microsoft/markitdown",
        license=license,
        stars=30000,
        description="convert documents to markdown",
        default_commit="a1b2c3d4e5f6",
    )


# ── full remote pipeline ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remote_pipeline_acquires_registers_and_wraps(
    postgres_dsn: str, tmp_path: Path
) -> None:
    await _reset(postgres_dsn)
    acq = _acquisition(postgres_dsn, "dev")
    await acq.initialize()
    runner = _FakeHostRunner()

    result = await acq.acquire_remote(
        _OWNER,
        _markitdown(),
        HostSpec(host="ai-prentice-2"),
        runner,
        commit="a1b2c3d4e5f6",
        solutions_root=tmp_path / "internal-solutions",
        visibility="shared",
        approval_callback=_APPROVE,
    )

    # Two human approvals were taken (§4.3: evaluate, then apply).
    assert result.approvals == 2
    assert result.tool_kind == "remote"
    assert result.change_ref is not None  # C5 change event recorded

    # The host was driven: commit-pinned clone off-box, non-root/restricted run.
    assert ("clone", "https://github.com/microsoft/markitdown", "a1b2c3d4e5f6",
            "/opt/data/internal-solutions/markitdown") in runner.calls
    run_call = next(c for c in runner.calls if c[0] == "run")
    assert run_call[3] is True and run_call[4] is True  # non_root, restricted

    # FG-07 remote tool row + FG-11 endpoint were registered (disabled in dev).
    tool = await acq.tools.get("markitdown")
    assert tool is not None
    assert tool.kind == "remote" and tool.mode == "dev" and tool.status == "disabled"
    assert tool.mcp_endpoint_ref == result.endpoint_name
    resolved = await acq.endpoints.resolve_server_configs(_OWNER)
    assert "markitdown" in resolved  # cache-safe: resolvable for a FUTURE session

    # Provenance pins repo/license/commit/host.
    prov = await acq.provenance.get("markitdown")
    assert prov is not None
    assert prov.source == "remote"
    assert prov.repo_url == "https://github.com/microsoft/markitdown"
    assert prov.license == "MIT" and prov.commit_sha == "a1b2c3d4e5f6"
    assert prov.host == "ai-prentice-2"

    # The generated fastmcp wrapper lives OUTSIDE the core repo tree and is a
    # reachable MCP server exposing the provenance tool.
    assert result.solution_root is not None
    server = Path(result.solution_root) / "solution_mcp.py"
    assert server.is_file()
    repo_root = Path(__file__).resolve().parents[2]
    assert repo_root not in server.resolve().parents
    module = _load_module(server)
    tool_names = {t.name for t in await module.mcp.list_tools()}
    assert {"provenance", "health"} <= tool_names
    assert module.provenance()["commit"] == "a1b2c3d4e5f6"


# ── hard rails: license, approvals, commit pin, run rails ────────────────────

@pytest.mark.asyncio
async def test_disallowed_license_registers_nothing(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    acq = _acquisition(postgres_dsn, "dev")
    await acq.initialize()

    with pytest.raises(LicenseNotAllowedError):
        await acq.acquire_remote(
            _OWNER,
            _markitdown(name="gpltool", license="GPL-3.0"),
            HostSpec(host="ai-prentice-2"),
            _FakeHostRunner(),
            commit="deadbeef",
            approval_callback=_APPROVE,
        )
    assert await acq.tools.get("gpltool") is None
    assert await acq.provenance.get("gpltool") is None


@pytest.mark.asyncio
async def test_unapproved_evaluate_blocks_and_never_runs(
    postgres_dsn: str,
) -> None:
    await _reset(postgres_dsn)
    acq = _acquisition(postgres_dsn, "dev")
    await acq.initialize()
    runner = _FakeHostRunner()

    with pytest.raises(PermissionError):
        await acq.acquire_remote(
            _OWNER,
            _markitdown(),
            HostSpec(host="ai-prentice-2"),
            runner,
            commit="deadbeef",
            approval_callback=_DENY,
        )
    # Denied at the evaluate gate: nothing cloned/run, nothing registered.
    assert runner.calls == []
    assert await acq.tools.get("markitdown") is None
    assert await acq.provenance.get("markitdown") is None


@pytest.mark.asyncio
async def test_unapproved_apply_blocks_registration(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    acq = _acquisition(postgres_dsn, "dev")
    await acq.initialize()
    runner = _FakeHostRunner()

    # Approve the first (evaluate) gate, deny the second (apply) gate.
    calls = {"n": 0}

    def _approve_then_deny(*_a, **_k):
        calls["n"] += 1
        return "once" if calls["n"] == 1 else "deny"

    with pytest.raises(PermissionError):
        await acq.acquire_remote(
            _OWNER,
            _markitdown(),
            HostSpec(host="ai-prentice-2"),
            runner,
            commit="deadbeef",
            approval_callback=_approve_then_deny,
        )
    # It vetted + ran (evaluate approved) but registered nothing (apply denied).
    assert any(c[0] == "run" for c in runner.calls)
    assert await acq.tools.get("markitdown") is None
    assert await acq.provenance.get("markitdown") is None


@pytest.mark.asyncio
async def test_missing_commit_pin_is_refused(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    acq = _acquisition(postgres_dsn, "dev")
    await acq.initialize()
    candidate = Candidate("nopin", "https://github.com/x/nopin", "MIT", 10)
    with pytest.raises(AcquisitionError):
        await acq.acquire_remote(
            _OWNER, candidate, HostSpec(host="h"), _FakeHostRunner(),
            approval_callback=_APPROVE,
        )


@pytest.mark.asyncio
async def test_root_or_unrestricted_host_is_refused(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    acq = _acquisition(postgres_dsn, "dev")
    await acq.initialize()
    with pytest.raises(AcquisitionError):
        await acq.acquire_remote(
            _OWNER, _markitdown(), HostSpec(host="h", non_root=False),
            _FakeHostRunner(), commit="c0ffee", approval_callback=_APPROVE,
        )


@pytest.mark.asyncio
async def test_viewer_cannot_acquire(postgres_dsn: str) -> None:
    await _reset(postgres_dsn)
    acq = _acquisition(postgres_dsn, "dev")
    await acq.initialize()
    with pytest.raises(PermissionError):
        await acq.acquire_remote(
            _VIEWER, _markitdown(), HostSpec(host="h"), _FakeHostRunner(),
            commit="c0ffee", approval_callback=_APPROVE,
        )


# ── in-house path (reuse FG-07 scaffolder) ───────────────────────────────────

@pytest.mark.asyncio
async def test_in_house_delegates_to_fg07_scaffolder(
    postgres_dsn: str, tmp_path: Path
) -> None:
    await _reset(postgres_dsn)
    acq = _acquisition(postgres_dsn, "dev")
    await acq.initialize()

    result = await acq.acquire_in_house(
        _OWNER, "invoicer", tools_root=tmp_path / "tools", visibility="shared",
        port=4555,
    )
    assert result.tool_kind == "in_house" and result.source == "in_house"

    tool = await acq.tools.get("invoicer")
    assert tool is not None
    assert tool.kind == "in_house" and tool.stack == "nextjs-node"
    assert tool.web_url == "http://127.0.0.1:4555"
    prov = await acq.provenance.get("invoicer")
    assert prov is not None and prov.source == "in_house"

    # The FG-07 scaffold really produced a Node MCP server that handshakes.
    server = tmp_path / "tools" / "invoicer" / "mcp" / "server.mjs"
    assert server.is_file()
    _assert_node_mcp_handshake(server)


# ── negative access (C2) + mode isolation (C3) ───────────────────────────────

@pytest.mark.asyncio
async def test_private_remote_system_negative_access(
    postgres_dsn: str, tmp_path: Path
) -> None:
    await _reset(postgres_dsn)
    acq = _acquisition(postgres_dsn, "dev")
    await acq.initialize()

    # Alice acquires a remote system PRIVATE to herself (default visibility).
    await acq.acquire_remote(
        _ALICE,
        _markitdown(name="alice_secret"),
        HostSpec(host="ai-prentice-2"),
        _FakeHostRunner(),
        commit="a1b2c3d4e5f6",
        solutions_root=tmp_path / "sol",
        approval_callback=_APPROVE,
    )

    # Bob (another member) can neither see the provenance nor the tool.
    assert "alice_secret" not in {
        p.tool_name for p in await acq.provenance.list_for_principal(_BOB)
    }
    assert "alice_secret" not in {
        t.name for t in await acq.tools.list_for_principal(_BOB)
    }
    # Alice sees her own; the owner sees it too (owner bypasses scoping).
    assert "alice_secret" in {
        p.tool_name for p in await acq.provenance.list_for_principal(_ALICE)
    }
    assert "alice_secret" in {
        p.tool_name for p in await acq.provenance.list_for_principal(_OWNER)
    }


@pytest.mark.asyncio
async def test_dev_acquisition_is_invisible_in_prod(
    postgres_dsn: str, tmp_path: Path
) -> None:
    await _reset(postgres_dsn)
    dev = _acquisition(postgres_dsn, "dev")
    prod = _acquisition(postgres_dsn, "prod")
    await dev.initialize()
    await prod.initialize()

    await dev.acquire_remote(
        _OWNER,
        _markitdown(name="dev_only_sys"),
        HostSpec(host="ai-prentice-2"),
        _FakeHostRunner(),
        commit="a1b2c3d4e5f6",
        solutions_root=tmp_path / "sol",
        visibility="shared",
        approval_callback=_APPROVE,
    )

    assert await dev.provenance.get("dev_only_sys") is not None
    assert await prod.provenance.get("dev_only_sys") is None
    assert await prod.tools.get("dev_only_sys") is None


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"gen_{path.stem}_{id(path)}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_node_mcp_handshake(server: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to exercise the in-house MCP server")
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hermes-test", "version": "0"},
            },
        }
    ) + "\n"
    proc = subprocess.run(
        ["node", str(server)],
        input=request, capture_output=True, text=True, timeout=30,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[0])
    assert payload["id"] == 1
    assert "serverInfo" in payload["result"]
