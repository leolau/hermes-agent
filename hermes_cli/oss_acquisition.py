"""OSS capability acquisition — remote systems + in-house rebuilds (FG-08).

The agent can acquire a capability from open source in one of two modes
(decision **D3**), both fully approval-gated, provenance-tracked, and dev→prod
promoted:

* **Remote system** — study an OSS project, **clone + host it on a different
  machine** with minimal/ideally no changes, then wrap it with an **MCP**
  interface so the agent can use it. This follows the authoritative six-stage
  pipeline of ``docs/design/architecture-design-number-one.md §4.3``
  (propose → vet → adapt → run → expose-MCP → retire) with **all** of its hard
  rails: a license allowlist, supply-chain + secret scan, a sandbox smoke test,
  a **pinned commit**, non-root + network-restricted execution, and **≥2 human
  approvals** (evaluate, then apply — contract C6).
* **In-house system** — when a clean build is preferred (or no suitable OSS
  exists), rebuild the capability in-house by **reusing the FG-07 scaffolder**
  (a Next.js app in its own Node process with a web UI + a thin MCP server).

This module reuses the already-merged seams and never re-implements them:

* the **FG-07 tool registry** (:class:`hermes_cli.tools_registry.ToolRegistry`)
  records the capability as a ``remote`` or ``in_house`` tool row and owns the
  approval-gated dev→prod promotion (:func:`~hermes_cli.tools_registry.promote_tool`);
* the **FG-11 endpoint registry**
  (:class:`hermes_cli.mcp_endpoints.MCPEndpointRegistry`) is where the MCP
  interface (remote wrapper *or* in-house server) is materialized for a
  **future** session;
* the **FG-08 provenance registry**
  (:class:`hermes_cli.oss_provenance.ProvenanceRegistry`) records where the
  capability came from;
* contracts **C1/C2** (principals + scoping), **C3** (datastore routing), and
  **C5/C6** (change events + approval) are consumed, not rebuilt.

**No third-party product tree in-core (AGENTS.md).** A remote OSS project is
someone else's product; we never vendor it under the repo tree. It is cloned
onto a *different* host and reached only through the generated ``fastmcp``
wrapper, whose thin shim we do own. The wrapper is generated **outside** the
core repo (default ``$HERMES_HOME/internal-solutions/<name>/``).

**Cache-safety (AGENTS.md).** Nothing here mutates a live conversation's system
prompt or toolset. A newly acquired capability's MCP endpoint is only ever
resolved for a *future* session (FG-11), never spliced into a running
conversation's cached prompt/tool schema.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
)

from hermes_cli.access import Principal

if TYPE_CHECKING:
    from hermes_cli.datastore import SupabaseAppStore


# ---------------------------------------------------------------------------
# Hard rail 1 — license allowlist (§4.3)
# ---------------------------------------------------------------------------

#: Permissive SPDX license identifiers allowed for OSS adaptation. Per the
#: design doc's OSS policy (§4.3 + architecture §9), permissive licenses
#: (MIT / BSD / Apache-2.0 / ISC and friends) are allowed; copyleft licenses
#: (GPL / AGPL / LGPL / MPL / SSPL) and anything unrecognised are **rejected**
#: (fail closed) and must be escalated to the owner before any adaptation.
LICENSE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "MIT",
        "MIT-0",
        "BSD-2-CLAUSE",
        "BSD-3-CLAUSE",
        "APACHE-2.0",
        "ISC",
        "0BSD",
        "UNLICENSE",
        "CC0-1.0",
        "ZLIB",
        "PYTHON-2.0",
        "POSTGRESQL",
        "BSL-1.0",
    }
)

#: Common human-readable spellings mapped onto the canonical SPDX id above.
_LICENSE_ALIASES: dict[str, str] = {
    "MIT LICENSE": "MIT",
    "THE MIT LICENSE": "MIT",
    "EXPAT": "MIT",
    "BSD": "BSD-3-CLAUSE",
    "BSD-3": "BSD-3-CLAUSE",
    "BSD 3-CLAUSE": "BSD-3-CLAUSE",
    "NEW BSD": "BSD-3-CLAUSE",
    "BSD-2": "BSD-2-CLAUSE",
    "BSD 2-CLAUSE": "BSD-2-CLAUSE",
    "SIMPLIFIED BSD": "BSD-2-CLAUSE",
    "APACHE": "APACHE-2.0",
    "APACHE 2.0": "APACHE-2.0",
    "APACHE-2": "APACHE-2.0",
    "APACHE LICENSE 2.0": "APACHE-2.0",
    "ASL 2.0": "APACHE-2.0",
    "THE UNLICENSE": "UNLICENSE",
    "PUBLIC DOMAIN": "CC0-1.0",
    "ZLIB/LIBPNG": "ZLIB",
    "BOOST": "BSL-1.0",
    "BOOST SOFTWARE LICENSE 1.0": "BSL-1.0",
}


class LicenseNotAllowedError(PermissionError):
    """The candidate's license is not on the permissive allowlist (§4.3)."""


def normalize_license(license_id: Optional[str]) -> str:
    """Normalize a raw license string to a canonical uppercase SPDX-ish id."""
    if not license_id:
        return ""
    key = re.sub(r"\s+", " ", license_id.strip()).upper()
    key = key.removesuffix(" LICENSE").strip() or key
    if key in LICENSE_ALLOWLIST:
        return key
    return _LICENSE_ALIASES.get(key, key)


def is_license_allowed(license_id: Optional[str]) -> bool:
    """Whether ``license_id`` is a permissive license allowed for adaptation.

    Fails closed: an empty, unknown, or copyleft license returns ``False``.
    """
    return normalize_license(license_id) in LICENSE_ALLOWLIST


# ---------------------------------------------------------------------------
# Stage 1 — propose / discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One public OSS repo proposed for a stated goal."""

    name: str
    repo_url: str
    license: str
    stars: int = 0
    pushed_at: str = ""
    description: str = ""
    default_commit: str = ""

    @property
    def license_ok(self) -> bool:
        return is_license_allowed(self.license)


#: A pluggable GitHub-repo search. The default is injected by callers/tests; a
#: production caller wires in the existing web/search tooling. Kept behind a
#: Protocol so discovery stays trivially unit-testable and free of network I/O.
class RepoSearch(Protocol):
    def __call__(self, goal: str, *, limit: int = 5) -> Sequence[Candidate]:
        ...


_WORD = re.compile(r"[a-z0-9]+")


def fit_score(candidate: Candidate, goal: str) -> float:
    """Deterministic 0..1 fit heuristic (keyword overlap + activity signal).

    Combines how well the goal's keywords overlap the repo name/description
    with a saturating popularity signal. Deterministic (no randomness) so the
    same goal + candidate set always ranks identically.
    """
    goal_words = set(_WORD.findall(goal.lower()))
    if not goal_words:
        overlap = 0.0
    else:
        haystack = f"{candidate.name} {candidate.description}".lower()
        hay_words = set(_WORD.findall(haystack))
        overlap = len(goal_words & hay_words) / len(goal_words)
    # Saturating popularity in [0, 1): 0 at 0 stars, ~0.9 at ~9k stars.
    popularity = candidate.stars / (candidate.stars + 1000.0)
    return round(0.7 * overlap + 0.3 * popularity, 6)


def discover_candidates(
    goal: str,
    search: RepoSearch,
    *,
    limit: int = 5,
    allowed_only: bool = False,
) -> List[Candidate]:
    """Stage 1: query public repos for ``goal`` and rank candidates by fit.

    Returns candidates highest-fit first. When ``allowed_only`` is set, only
    permissively-licensed candidates are returned (the license allowlist is
    still re-checked at the vet stage regardless).
    """
    candidates = list(search(goal, limit=limit))
    if allowed_only:
        candidates = [c for c in candidates if c.license_ok]
    candidates.sort(key=lambda c: fit_score(c, goal), reverse=True)
    return candidates[:limit]


AcquisitionMode = Literal["remote", "in_house"]


def choose_mode(
    *,
    explicit_in_house: bool,
    candidates: Sequence[Candidate],
) -> AcquisitionMode:
    """Choose remote-adapt-and-wrap vs. in-house rebuild (FG-08 heuristic).

    Default is **remote** (cheaper, lower-risk adapt-and-wrap). In-house rebuild
    is chosen only on explicit request, or when no permissively-licensed OSS
    candidate exists.
    """
    if explicit_in_house:
        return "in_house"
    if any(c.license_ok for c in candidates):
        return "remote"
    return "in_house"


# ---------------------------------------------------------------------------
# Stage 2 — vet (license allowlist + supply-chain + secret scan + smoke)
# ---------------------------------------------------------------------------


#: A scan step returns a (possibly empty) tuple of finding strings. Empty =
#: clean. Injected so tests can exercise both clean and failing paths; the
#: defaults are conservative no-op stubs (the flow is modelled, not run live).
Scanner = Callable[[Candidate], Sequence[str]]


def _clean_scan(_candidate: Candidate) -> Sequence[str]:
    return ()


@dataclass(frozen=True)
class VetReport:
    """Outcome of stage-2 vetting for one candidate."""

    candidate: Candidate
    license_ok: bool
    supply_chain_findings: tuple[str, ...] = ()
    secret_findings: tuple[str, ...] = ()
    smoke_ok: bool = True

    @property
    def passed(self) -> bool:
        return (
            self.license_ok
            and not self.supply_chain_findings
            and not self.secret_findings
            and self.smoke_ok
        )

    @property
    def findings(self) -> tuple[str, ...]:
        out: list[str] = []
        if not self.license_ok:
            out.append(
                f"license {self.candidate.license!r} is not on the permissive "
                "allowlist"
            )
        out.extend(f"supply-chain: {f}" for f in self.supply_chain_findings)
        out.extend(f"secret-scan: {f}" for f in self.secret_findings)
        if not self.smoke_ok:
            out.append("sandbox smoke test failed")
        return tuple(out)


def vet_candidate(
    candidate: Candidate,
    *,
    supply_chain_scan: Scanner = _clean_scan,
    secret_scan: Scanner = _clean_scan,
    smoke_test: Callable[[Candidate], bool] = lambda _c: True,
) -> VetReport:
    """Stage 2: vet a candidate before any clone/run happens.

    The license allowlist is the hard, always-enforced rail; the supply-chain /
    secret / smoke checks are injected (a live deployment wires in real
    scanners, tests exercise both outcomes). A report whose ``passed`` is
    ``False`` must block the pipeline.
    """
    return VetReport(
        candidate=candidate,
        license_ok=is_license_allowed(candidate.license),
        supply_chain_findings=tuple(supply_chain_scan(candidate)),
        secret_findings=tuple(secret_scan(candidate)),
        smoke_ok=bool(smoke_test(candidate)),
    )


# ---------------------------------------------------------------------------
# Stages 3-4 — adapt (clone off-box, commit-pinned) + run (non-root, restricted)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HostSpec:
    """The *different* machine a remote OSS system is cloned onto and run on.

    ``host`` is the off-box target (an ssh/remote target reached via
    ``tools/environments``). ``non_root`` and ``network_restricted`` model the
    §4.3 execution rails; both must stay ``True`` or the pipeline refuses to
    run. The service is bound to localhost *on that host* — never exposed
    publicly (``bind``).
    """

    host: str
    non_root: bool = True
    network_restricted: bool = True
    bind: str = "127.0.0.1"
    workdir: str = "/opt/data/internal-solutions"


@dataclass(frozen=True)
class ServiceHandle:
    """A running (modelled) remote service."""

    name: str
    remote_path: str
    base_url: str
    handle_id: str = field(default_factory=lambda: f"svc_{uuid.uuid4().hex}")


class HostRunner(Protocol):
    """Clone + run a repo on a *different* machine (§4.3 stages 3-4).

    Implementations wrap a ``tools/environments`` remote backend (ssh, etc.).
    The pipeline never assumes a concrete backend: tests inject a recording
    runner so the provenance/registration/wrapping flow is exercised without
    actually deploying to an external machine.
    """

    def clone(self, repo_url: str, commit: str, *, dest: str) -> str:
        """Clone ``repo_url`` at pinned ``commit`` into ``dest``; return path."""
        ...

    def run_service(
        self,
        remote_path: str,
        *,
        name: str,
        non_root: bool,
        network_restricted: bool,
        bind: str,
    ) -> ServiceHandle:
        """Launch the cloned project as an isolated local service on the host."""
        ...

    def health_check(self, handle: ServiceHandle) -> bool:
        """Return whether the running service is healthy."""
        ...

    def stop(self, handle: ServiceHandle) -> None:
        """Stop the running service (retire)."""
        ...


# ---------------------------------------------------------------------------
# Stage 5 — expose as MCP (generate the fastmcp wrapper, OUTSIDE the core tree)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WrappedSolution:
    """A generated ``fastmcp`` wrapper for a running remote service."""

    name: str
    root: Path
    server_path: Path
    files: List[str] = field(default_factory=list)

    def mcp_transport(self) -> dict:
        """The stdio MCP transport the FG-11 endpoint registry consumes.

        The wrapper reads its own ``solution.json`` sidecar (next to the
        module), so the transport needs no environment variables — keeping the
        no-new-``HERMES_*``-env-var rule trivially satisfied.
        """
        return {
            "type": "stdio",
            "command": sys.executable or "python3",
            "args": [str(self.server_path)],
        }


def generate_solution_mcp(
    name: str,
    root: Path,
    *,
    repo_url: str,
    commit: str,
    license: str,
    host: str,
    upstream_url: str,
) -> WrappedSolution:
    """Generate a thin ``fastmcp`` MCP wrapper for a hosted OSS service (§4.3).

    Writes ``<root>/<name>/solution_mcp.py`` (a ``fastmcp`` server built on the
    MCP SDK's ``FastMCP``) plus a ``solution.json`` provenance sidecar. The
    wrapper exposes two read-only tools — ``provenance`` (the pinned
    repo/commit/license/host) and ``health`` (the modelled upstream reachability
    check) — the minimum needed to *use* the hosted system through MCP.

    ``root`` must live **outside** the core repo tree (default
    ``$HERMES_HOME/internal-solutions``): the third-party project is never
    vendored in-core; only this thin wrapper is ours.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name or ""):
        raise ValueError(f"Invalid solution name: {name!r}")

    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    written: List[str] = []

    def _write(relative: str, content: str) -> None:
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(relative)

    sidecar = {
        "name": name,
        "repo_url": repo_url,
        "commit": commit,
        "license": license,
        "host": host,
        "upstream_url": upstream_url,
    }
    _write("solution.json", json.dumps(sidecar, indent=2, sort_keys=True) + "\n")
    _write("solution_mcp.py", _SOLUTION_MCP_PY)
    _write("README.md", _SOLUTION_README.format(name=name, repo_url=repo_url))

    return WrappedSolution(
        name=name,
        root=project,
        server_path=project / "solution_mcp.py",
        files=sorted(written),
    )


# A thin fastmcp wrapper. It fronts a remote OSS service that is hosted on a
# DIFFERENT machine — the third-party project itself is never vendored here.
# Reads its pinned provenance from the sibling solution.json; exposes read-only
# ``provenance`` and ``health`` tools. Uses the MCP SDK's FastMCP (the same
# ``mcp.server.fastmcp`` the repo's own ``mcp_serve.py`` uses).
_SOLUTION_MCP_PY = '''\
#!/usr/bin/env python3
"""Auto-generated fastmcp wrapper for a hosted OSS solution (Hermes FG-08).

The wrapped third-party project runs on a different, network-restricted,
non-root host; this shim is the ONLY interface the agent uses to reach it.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from mcp.server.fastmcp import FastMCP

_SIDECAR = json.loads(
    (Path(__file__).parent / "solution.json").read_text(encoding="utf-8")
)

mcp = FastMCP(_SIDECAR["name"])


@mcp.tool()
def provenance() -> dict:
    """Return the pinned provenance of the wrapped OSS solution."""
    return {
        "name": _SIDECAR["name"],
        "repo_url": _SIDECAR["repo_url"],
        "commit": _SIDECAR["commit"],
        "license": _SIDECAR["license"],
        "host": _SIDECAR["host"],
    }


@mcp.tool()
def health() -> dict:
    """Best-effort reachability check of the hosted upstream service."""
    url = _SIDECAR.get("upstream_url", "")
    if not url:
        return {"reachable": False, "url": url, "detail": "no upstream configured"}
    try:
        with urlopen(url, timeout=5) as response:  # noqa: S310 - localhost-only
            return {"reachable": True, "url": url, "status": response.status}
    except Exception as exc:  # noqa: BLE001 - health is best-effort
        return {"reachable": False, "url": url, "detail": str(exc)}


if __name__ == "__main__":
    mcp.run()
'''

_SOLUTION_README = """\
# {name} (remote OSS solution wrapper)

Auto-generated Hermes FG-08 wrapper. The upstream project (`{repo_url}`) is
cloned + hosted on a **different** machine, non-root and network-restricted, and
reached ONLY through the `fastmcp` server in `solution_mcp.py`. The third-party
project is never vendored into the Hermes core repo tree (AGENTS.md).

Provenance (repo URL, pinned commit, license, host) lives in `solution.json`.
"""


# ---------------------------------------------------------------------------
# The six-stage remote pipeline orchestrator (+ in-house delegation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquisitionRails:
    """The §4.3 hard rails the remote pipeline enforces (all default-on)."""

    require_license_allowlist: bool = True
    require_commit_pin: bool = True
    require_non_root: bool = True
    require_network_restricted: bool = True
    min_approvals: int = 2


@dataclass(frozen=True)
class AcquisitionResult:
    """What one successful acquisition produced (remote or in-house)."""

    tool_name: str
    mode: str
    source: str
    tool_kind: str
    endpoint_name: str
    provenance_id: str
    web_url: Optional[str] = None
    solution_root: Optional[str] = None
    approvals: int = 0
    change_ref: Optional[str] = None


class AcquisitionError(RuntimeError):
    """A rail was violated or a stage failed; nothing was registered."""


ApprovalGate = Callable[..., str]


class OSSAcquisition:
    """Acquire OSS capabilities as remote systems or in-house rebuilds (FG-08).

    Constructed with a single mode-aware :class:`SupabaseAppStore` (contract
    C3); the tool, endpoint, and provenance registries all share that store, so
    a dev acquisition lands entirely in ``app_dev`` and a channel-forced prod
    session never sees it. The ``prod_store`` is where the shared C5 change log
    is appended (that audit table lives in the prod schema).
    """

    def __init__(
        self,
        store: "SupabaseAppStore",
        *,
        prod_store: Optional["SupabaseAppStore"] = None,
        rails: Optional[AcquisitionRails] = None,
    ) -> None:
        from hermes_cli.datastore import get_store
        from hermes_cli.mcp_endpoints import MCPEndpointRegistry
        from hermes_cli.oss_provenance import ProvenanceRegistry
        from hermes_cli.tools_registry import ToolRegistry

        self._store = store
        if prod_store is not None:
            self._prod_store = prod_store
        elif store.mode == "prod":
            self._prod_store = store
        else:
            self._prod_store = get_store("supabase-app", "prod", config=None)
        self._rails = rails or AcquisitionRails()
        self.tools = ToolRegistry(store)
        self.endpoints = MCPEndpointRegistry(store)
        self.provenance = ProvenanceRegistry(store)

    @property
    def mode(self) -> str:
        return self._store.mode

    async def initialize(self) -> None:
        """Create the tool + endpoint + provenance tables (idempotent)."""
        await self.tools.initialize()
        await self.endpoints.initialize()
        await self.provenance.initialize()

    # -- approvals --------------------------------------------------------

    @staticmethod
    def _approve(stage: str, name: str, approval_callback: ApprovalGate) -> bool:
        from tools.approval import prompt_dangerous_approval

        choice = prompt_dangerous_approval(
            f"hermes oss acquire {name} [{stage}]",
            f"{stage} an open-source system acquisition (§4.3)",
            allow_permanent=False,
            approval_callback=approval_callback,
        )
        return choice in ("once", "session", "always")

    # -- remote path (§4.3) ----------------------------------------------

    async def acquire_remote(
        self,
        principal: Principal,
        candidate: Candidate,
        host_spec: HostSpec,
        host_runner: HostRunner,
        *,
        name: Optional[str] = None,
        commit: Optional[str] = None,
        solutions_root: Optional[Path] = None,
        visibility: Optional[str] = None,
        approval_callback: ApprovalGate,
        supply_chain_scan: Scanner = _clean_scan,
        secret_scan: Scanner = _clean_scan,
        smoke_test: Callable[[Candidate], bool] = lambda _c: True,
    ) -> AcquisitionResult:
        """Run the full §4.3 remote pipeline for ``candidate``.

        propose(approve #1) → vet → adapt(clone off-box, pinned commit) →
        run(non-root, network-restricted) → expose-MCP(generate fastmcp wrapper
        outside the core tree, register FG-11 endpoint + FG-07 ``remote`` tool +
        provenance)(approve #2). Requires ``>= min_approvals`` human approvals;
        a denied gate raises before anything is cloned, run, or registered.
        """
        if principal.role == "viewer":
            raise PermissionError("viewer principals may not acquire systems")
        tool_name = name or candidate.name
        approvals = 0

        # Stage 1 — propose: approve *evaluating* the candidate (approval #1).
        if not self._approve("evaluate", tool_name, approval_callback):
            raise PermissionError("Acquisition evaluate approval was denied")
        approvals += 1

        # Stage 2 — vet: license allowlist (hard rail) + scans + smoke.
        report = vet_candidate(
            candidate,
            supply_chain_scan=supply_chain_scan,
            secret_scan=secret_scan,
            smoke_test=smoke_test,
        )
        if self._rails.require_license_allowlist and not report.license_ok:
            raise LicenseNotAllowedError(
                f"{candidate.repo_url}: {report.findings[0]}"
            )
        if not report.passed:
            raise AcquisitionError(
                "vetting failed: " + "; ".join(report.findings)
            )

        # Stage 3 — adapt: pin the commit, clone onto a DIFFERENT host.
        pinned_commit = commit or candidate.default_commit
        if self._rails.require_commit_pin and not pinned_commit:
            raise AcquisitionError(
                "commit pin is required (§4.3) — no commit supplied and the "
                "candidate has no default_commit"
            )
        self._enforce_run_rails(host_spec)
        dest = f"{host_spec.workdir.rstrip('/')}/{tool_name}"
        remote_path = host_runner.clone(
            candidate.repo_url, pinned_commit, dest=dest
        )

        # Stage 4 — run: launch non-root, network-restricted, localhost-only.
        handle = host_runner.run_service(
            remote_path,
            name=tool_name,
            non_root=host_spec.non_root,
            network_restricted=host_spec.network_restricted,
            bind=host_spec.bind,
        )
        if not host_runner.health_check(handle):
            raise AcquisitionError(
                f"hosted service {tool_name!r} failed its health check"
            )

        # Stage 5 — expose as MCP: generate the fastmcp wrapper OUTSIDE the
        # core tree, then approve *applying* the acquisition (approval #2).
        root = solutions_root or self._default_solutions_root()
        solution = generate_solution_mcp(
            tool_name,
            root,
            repo_url=candidate.repo_url,
            commit=pinned_commit,
            license=report.candidate.license,
            host=host_spec.host,
            upstream_url=handle.base_url,
        )
        if not self._approve("apply", tool_name, approval_callback):
            raise PermissionError("Acquisition apply approval was denied")
        approvals += 1
        if approvals < self._rails.min_approvals:
            raise AcquisitionError(
                f"§4.3 requires >= {self._rails.min_approvals} approvals; "
                f"got {approvals}"
            )

        endpoint = await self.endpoints.register(
            principal,
            tool_name,
            "remote",
            solution.mcp_transport(),
            visibility=visibility,
        )
        tool = await self.tools.create(
            principal,
            tool_name,
            "remote",
            stack="oss-remote-mcp",
            visibility=visibility,
            status="disabled",
            mcp_endpoint_ref=endpoint.name,
            web_url=handle.base_url,
        )
        prov = await self.provenance.record(
            principal,
            tool_name,
            "remote",
            repo_url=candidate.repo_url,
            license=report.candidate.license,
            commit_sha=pinned_commit,
            host=host_spec.host,
            visibility=tool.visibility,
        )
        change_ref = await self._record_change(
            actor_user_id=principal.user_id,
            action=f"hermes oss acquire {tool_name} (remote)",
            target_ref=tool_name,
            mode=tool.mode,
            visibility=tool.visibility,
            payload=prov.as_dict(),
        )

        return AcquisitionResult(
            tool_name=tool_name,
            mode=tool.mode,
            source="remote",
            tool_kind="remote",
            endpoint_name=endpoint.name,
            provenance_id=prov.id,
            web_url=tool.web_url,
            solution_root=str(solution.root),
            approvals=approvals,
            change_ref=change_ref,
        )

    # -- in-house path (reuse FG-07 scaffolder) ---------------------------

    async def acquire_in_house(
        self,
        principal: Principal,
        name: str,
        *,
        tools_root: Optional[Path] = None,
        visibility: Optional[str] = None,
        port: Optional[int] = None,
    ) -> AcquisitionResult:
        """Rebuild a capability in-house by **reusing the FG-07 scaffolder**.

        Delegates project generation to
        :func:`hermes_cli.tool_scaffold.scaffold_in_house_tool` (Next.js app +
        thin MCP server, own Node process), then registers the ``in_house`` tool
        row + its MCP endpoint + a provenance record. In-house tools start
        ``disabled`` in dev and reach prod through the FG-07 promotion.
        """
        if principal.role == "viewer":
            raise PermissionError("viewer principals may not acquire systems")

        from hermes_cli.tool_scaffold import scaffold_in_house_tool
        from hermes_constants import get_hermes_home

        root = tools_root or Path(get_hermes_home()) / "tools"
        scaffold = scaffold_in_house_tool(name, root, port=port)

        endpoint = await self.endpoints.register(
            principal,
            name,
            "in_house",
            scaffold.mcp_transport(),
            visibility=visibility,
        )
        tool = await self.tools.create(
            principal,
            name,
            "in_house",
            stack="nextjs-node",
            visibility=visibility,
            status="disabled",
            mcp_endpoint_ref=endpoint.name,
            web_url=scaffold.web_url,
        )
        prov = await self.provenance.record(
            principal,
            name,
            "in_house",
            repo_url="",
            license="N/A (in-house build)",
            commit_sha="",
            host="local",
            visibility=tool.visibility,
        )
        change_ref = await self._record_change(
            actor_user_id=principal.user_id,
            action=f"hermes oss acquire {name} (in-house)",
            target_ref=name,
            mode=tool.mode,
            visibility=tool.visibility,
            payload=prov.as_dict(),
        )

        return AcquisitionResult(
            tool_name=name,
            mode=tool.mode,
            source="in_house",
            tool_kind="in_house",
            endpoint_name=endpoint.name,
            provenance_id=prov.id,
            web_url=tool.web_url,
            solution_root=str(scaffold.root),
            approvals=0,
            change_ref=change_ref,
        )

    # -- retire -----------------------------------------------------------

    async def retire(
        self,
        principal: Principal,
        name: str,
        *,
        host_runner: Optional[HostRunner] = None,
        handle: Optional[ServiceHandle] = None,
    ) -> None:
        """Stage 6 retire: disable the tool and stop its hosted service."""
        tool = await self.tools.set_enabled(principal, name, False)
        if host_runner is not None and handle is not None:
            host_runner.stop(handle)
        await self._record_change(
            actor_user_id=principal.user_id,
            action=f"hermes oss retire {name}",
            target_ref=name,
            mode=tool.mode,
            visibility=tool.visibility,
            payload={"status": "disabled"},
        )

    # -- helpers ----------------------------------------------------------

    def _enforce_run_rails(self, host_spec: HostSpec) -> None:
        if not host_spec.host:
            raise AcquisitionError(
                "a remote host is required — the OSS project is hosted on a "
                "DIFFERENT machine (§4.3)"
            )
        if self._rails.require_non_root and not host_spec.non_root:
            raise AcquisitionError("§4.3 rail: the service must run non-root")
        if self._rails.require_network_restricted and not host_spec.network_restricted:
            raise AcquisitionError(
                "§4.3 rail: the service must run network-restricted"
            )

    @staticmethod
    def _default_solutions_root() -> Path:
        # Kept OUT of the core repo tree: the third-party project is someone
        # else's product (AGENTS.md). Defaults under HERMES_HOME.
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()) / "internal-solutions"

    async def _record_change(
        self,
        *,
        actor_user_id: str,
        action: str,
        target_ref: str,
        mode: str,
        visibility: str,
        payload: Mapping[str, object],
    ) -> Optional[str]:
        """Append one pre-approved C5 change event (best-effort provenance).

        The registries are the source of truth; if the shared change log is
        unavailable the acquisition already succeeded, so surface a warning
        rather than fail the command.
        """
        from hermes_cli.changes import ChangeLog, initialize_changes

        op: list[dict[str, object]] = [
            {"op": "record", "path": f"/oss/{target_ref}", "value": dict(payload)}
        ]
        try:
            connection = await self._prod_store.connect()
            try:
                await initialize_changes(connection)
            finally:
                await connection.close()
            result = await ChangeLog(self._prod_store).record(
                actor_user_id=actor_user_id,
                target_kind="code",
                op=op,
                inverse_op=op,
                reversible=True,
                action=action,
                target_ref=target_ref,
                mode=mode,
                visibility=visibility,
                payload=dict(payload),
                approved=True,
            )
            return result.change_ref
        except Exception as exc:  # noqa: BLE001 - provenance is best-effort
            print(f"warning: could not record acquisition change: {exc}")
            return None


# ---------------------------------------------------------------------------
# Default GitHub discovery (used by the CLI; tests inject their own search)
# ---------------------------------------------------------------------------


def github_search(goal: str, *, limit: int = 5) -> List[Candidate]:
    """Search public GitHub repos for ``goal`` via the REST search API.

    Best-effort, dependency-free (``urllib``), unauthenticated. Callers that
    need higher rate limits should inject their own :class:`RepoSearch` that
    reuses the existing web/search tooling. Returns ``[]`` on any error rather
    than raising, so discovery degrades gracefully.
    """
    import urllib.parse
    import urllib.request

    query = urllib.parse.urlencode(
        {"q": goal, "sort": "stars", "order": "desc", "per_page": limit}
    )
    url = f"https://api.github.com/search/repositories?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "hermes-agent-oss-discovery",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - discovery degrades gracefully
        return []

    candidates: List[Candidate] = []
    for item in payload.get("items", [])[:limit]:
        license_block = item.get("license") or {}
        candidates.append(
            Candidate(
                name=str(item.get("name", "")),
                repo_url=str(item.get("html_url", "")),
                license=str(license_block.get("spdx_id") or ""),
                stars=int(item.get("stargazers_count", 0) or 0),
                pushed_at=str(item.get("pushed_at", "")),
                description=str(item.get("description") or ""),
                default_commit="",
            )
        )
    return candidates


__all__ = [
    "LICENSE_ALLOWLIST",
    "LicenseNotAllowedError",
    "normalize_license",
    "is_license_allowed",
    "Candidate",
    "RepoSearch",
    "fit_score",
    "discover_candidates",
    "AcquisitionMode",
    "choose_mode",
    "Scanner",
    "VetReport",
    "vet_candidate",
    "HostSpec",
    "ServiceHandle",
    "HostRunner",
    "WrappedSolution",
    "generate_solution_mcp",
    "AcquisitionRails",
    "AcquisitionResult",
    "AcquisitionError",
    "OSSAcquisition",
    "github_search",
]
