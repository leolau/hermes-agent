"""``hermes oss`` — acquire OSS capabilities as remote systems or in-house builds.

The CLI rung of the footprint ladder for FG-08: acquiring a capability from
open source (either an approval-gated remote adapt-and-wrap per architecture
§4.3, or an in-house rebuild reusing the FG-07 scaffolder) is expressed as
``hermes oss <sub>`` commands — **zero** model-tool footprint, no new core
surface, and nothing that mutates a live conversation's prompt/toolset.

Subcommands:

* ``discover <goal>`` — search public GitHub repos for a goal and rank
  candidates by fit + license (stage 1, propose).
* ``acquire <name>`` — run the acquisition. ``--in-house`` rebuilds via the
  FG-07 scaffolder; otherwise the §4.3 remote pipeline runs against ``--host``
  (approval-gated: ≥2 human approvals, license allowlist, commit-pinned,
  non-root + network-restricted).
* ``list`` — list acquired-capability provenance visible to the operator.
* ``retire <name>`` — disable an acquired capability (stage 6).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from hermes_cli.access import Principal


async def _resolve_operator(store, as_user: Optional[str]) -> Principal:
    """Resolve the operator principal: ``--as <user>`` or the enrolled owner."""
    from hermes_cli.access import PrincipalStore

    principals = PrincipalStore(store)
    if as_user:
        principal = await principals.get(as_user)
        if principal is None:
            raise SystemExit(
                f"No principal enrolled for {as_user!r}. Enrol the user first."
            )
        return principal
    owner = await principals.get_owner()
    if owner is None:
        raise SystemExit(
            "No owner enrolled yet — enrol the owner first, or pass --as <user>."
        )
    return owner


def cmd_oss(args) -> None:
    """Dispatch ``hermes oss`` subcommands (default ``list``)."""
    action = getattr(args, "oss_action", None) or "list"
    asyncio.run(_run(args, action))


async def _run(args, action: str) -> None:
    from hermes_cli.datastore import get_store, resolve_mode

    if action == "discover":
        _cmd_discover(args)
        return

    mode = getattr(args, "mode", None) or resolve_mode(None)
    prod_store = get_store("supabase-app", "prod")
    principal = await _resolve_operator(prod_store, getattr(args, "as_user", None))

    from hermes_cli.oss_acquisition import OSSAcquisition

    acquisition = OSSAcquisition(get_store("supabase-app", mode), prod_store=prod_store)
    await acquisition.initialize()

    if action == "acquire":
        await _cmd_acquire(args, acquisition, principal, mode)
    elif action == "list":
        await _cmd_list(acquisition, principal, mode)
    elif action == "retire":
        await acquisition.retire(principal, args.name)
        print(f"Retired acquired capability {args.name!r} (mode={mode}).")


def _cmd_discover(args) -> None:
    from hermes_cli.oss_acquisition import discover_candidates, github_search

    candidates = discover_candidates(
        args.goal, github_search, limit=args.limit, allowed_only=args.allowed_only
    )
    if not candidates:
        print(f"No candidates found for goal {args.goal!r}.")
        return
    print(f"Candidates for {args.goal!r} (highest-fit first):")
    for candidate in candidates:
        flag = "allowed" if candidate.license_ok else "LICENSE-REJECTED"
        print(
            f"  - {candidate.name}  ⭐{candidate.stars}  "
            f"license={candidate.license or '?'} [{flag}]\n"
            f"      {candidate.repo_url}\n"
            f"      {candidate.description}"
        )


async def _cmd_acquire(args, acquisition, principal, mode) -> None:
    visibility = "shared" if getattr(args, "shared", False) else None

    if getattr(args, "in_house", False):
        root = Path(args.root) if getattr(args, "root", None) else None
        result = await acquisition.acquire_in_house(
            principal, args.name, tools_root=root, visibility=visibility
        )
        print(
            f"Rebuilt in-house capability {result.tool_name!r} (mode={result.mode}) "
            f"via the FG-07 scaffolder."
        )
    else:
        from hermes_cli.oss_acquisition import Candidate, HostSpec
        from hermes_cli.oss_host import SSHHostRunner

        if not args.repo or not args.host:
            raise SystemExit(
                "Remote acquisition requires --repo and --host (the OSS project "
                "is hosted on a DIFFERENT machine). Use --in-house to rebuild."
            )
        candidate = Candidate(
            name=args.name,
            repo_url=args.repo,
            license=args.license or "",
            stars=int(args.stars or 0),
            description=args.description or "",
            default_commit=args.commit or "",
        )
        host_spec = HostSpec(host=args.host, workdir=args.workdir)
        runner = SSHHostRunner(
            host=args.host,
            user=args.ssh_user,
            start_cmd=args.start_cmd,
            health_url=args.health_url,
        )
        result = await acquisition.acquire_remote(
            principal,
            candidate,
            host_spec,
            runner,
            name=args.name,
            commit=args.commit,
            visibility=visibility,
            approval_callback=None,
        )
        print(
            f"Acquired remote OSS system {result.tool_name!r} (mode={result.mode}) "
            f"with {result.approvals} approvals.\n"
            f"  wrapper: {result.solution_root}\n"
            f"  upstream: {result.web_url}"
        )
    print(
        f"  endpoint={result.endpoint_name}  provenance={result.provenance_id}\n"
        f"Registered DISABLED in {mode}. Enable with `hermes tool enable "
        f"{result.tool_name} --mode {mode}`, then `hermes tool promote "
        f"{result.tool_name}`."
    )


async def _cmd_list(acquisition, principal, mode) -> None:
    rows = await acquisition.provenance.list_for_principal(principal)
    if not rows:
        print(f"No acquired capabilities visible to {principal.user_id!r} in {mode}.")
        return
    print(f"Acquired capabilities visible to {principal.user_id!r} (mode={mode}):")
    for row in rows:
        detail = (
            f"repo={row.repo_url} @ {row.commit_sha[:12] or '-'} host={row.host}"
            if row.source == "remote"
            else "in-house rebuild"
        )
        print(
            f"  - {row.tool_name}  [{row.source}]  license={row.license or '-'}  "
            f"visibility={row.visibility}\n      {detail}"
        )


def register_oss_subparser(subparsers) -> None:
    """Wire the ``hermes oss`` parser (FG-08 OSS acquisition)."""
    parser = subparsers.add_parser(
        "oss",
        help="Acquire OSS capabilities (remote adapt-and-wrap or in-house) (FG-08)",
        description=(
            "Acquire a capability from open source: a remote system (clone + "
            "host an OSS project on a DIFFERENT machine, wrapped as MCP, §4.3) "
            "or an in-house rebuild (reusing the FG-07 scaffolder). Fully "
            "approval-gated and provenance-tracked."
        ),
    )
    parser.add_argument(
        "--as", dest="as_user", default=None,
        help="Operate as this user_id (defaults to the enrolled owner)",
    )
    parser.add_argument(
        "--mode", choices=("dev", "prod"), default=None,
        help="Datastore mode (defaults to config datastore.mode / prod)",
    )
    sub = parser.add_subparsers(dest="oss_action")

    discover = sub.add_parser("discover", help="Search public repos for a goal")
    discover.add_argument("goal", help="What capability you need")
    discover.add_argument("--limit", type=int, default=5)
    discover.add_argument(
        "--allowed-only", action="store_true",
        help="Only show permissively-licensed candidates",
    )
    discover.set_defaults(func=cmd_oss, oss_action="discover")

    acquire = sub.add_parser("acquire", help="Acquire a capability (remote/in-house)")
    acquire.add_argument("name", help="Capability name (registry + wrapper dir)")
    acquire.add_argument(
        "--in-house", action="store_true",
        help="Rebuild in-house via the FG-07 scaffolder instead of remote-wrap",
    )
    acquire.add_argument("--repo", default=None, help="OSS repo URL (remote path)")
    acquire.add_argument("--license", default=None, help="Declared SPDX license")
    acquire.add_argument("--commit", default=None, help="Commit to pin (§4.3)")
    acquire.add_argument("--host", default=None, help="Different machine to host on")
    acquire.add_argument("--ssh-user", default=None, help="SSH user on --host")
    acquire.add_argument(
        "--workdir", default="/opt/data/internal-solutions",
        help="Off-box directory the project is cloned into",
    )
    acquire.add_argument("--start-cmd", default=None, help="Command to start the service")
    acquire.add_argument("--health-url", default=None, help="Service health URL")
    acquire.add_argument("--stars", default=None, help="Repo star count (metadata)")
    acquire.add_argument("--description", default=None, help="Repo description")
    acquire.add_argument("--root", default=None, help="Scaffold root (in-house)")
    acquire.add_argument(
        "--shared", action="store_true",
        help="Register as shared instead of private to the operator",
    )
    acquire.set_defaults(func=cmd_oss, oss_action="acquire")

    listp = sub.add_parser("list", help="List acquired-capability provenance")
    listp.set_defaults(func=cmd_oss, oss_action="list")

    retire = sub.add_parser("retire", help="Disable an acquired capability")
    retire.add_argument("name")
    retire.set_defaults(func=cmd_oss, oss_action="retire")

    parser.set_defaults(func=cmd_oss)


__all__ = ["cmd_oss", "register_oss_subparser"]
