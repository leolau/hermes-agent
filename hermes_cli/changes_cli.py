"""``hermes changes`` — review and reverse recorded change events (FG-12).

A thin CLI edge over :class:`hermes_cli.changes.ChangeLog` (contract C5) so the
change log is reviewable/undoable without adding a core model tool (footprint
ladder rung 2: CLI command + skill). All actions are scoped to the ``--actor``
principal via contract C2: a member sees/undoes only shared rows and their own
private rows; the owner sees/undoes everything.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from hermes_cli.access import Principal, PrincipalStore
from hermes_cli.changes import ChangeError, ChangeLog
from hermes_cli.datastore import get_store


def _log() -> ChangeLog:
    return ChangeLog(get_store("supabase-app", "prod"))


async def _resolve_principal(actor: str) -> Principal:
    store = PrincipalStore(get_store("supabase-app", "prod"))
    principal = await store.get(actor)
    if principal is None:
        raise ChangeError(f"Unknown principal: {actor!r} (enroll it first)")
    return principal


def changes_list_command(args: argparse.Namespace) -> int:
    try:
        principal = asyncio.run(_resolve_principal(args.actor))
        events = asyncio.run(
            _log().list_changes(
                principal, include_undone=not args.active_only, limit=args.limit
            )
        )
    except (ChangeError, RuntimeError, ValueError) as error:
        print(f"Could not list changes: {error}", file=sys.stderr)
        return 1
    if not events:
        print("No changes visible to this principal.")
        return 0
    for event in events:
        state = "undone" if event.undone else "applied"
        flag = "reversible" if event.reversible else "IRREVERSIBLE"
        print(
            f"{event.id}  {event.target_kind:<6}  {state:<7}  {flag:<11}  "
            f"{event.visibility}  actor={event.actor_user_id}"
        )
    return 0


def changes_undo_command(args: argparse.Namespace) -> int:
    try:
        principal = asyncio.run(_resolve_principal(args.actor))
        result = asyncio.run(_log().undo(args.change_ref, principal))
    except PermissionError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    except (ChangeError, RuntimeError, ValueError) as error:
        print(f"Undo failed: {error}", file=sys.stderr)
        return 1
    print(f"Undid {result.change_ref} ({result.target_kind}): {result.detail}")
    return 0


def changes_redo_command(args: argparse.Namespace) -> int:
    try:
        principal = asyncio.run(_resolve_principal(args.actor))
        result = asyncio.run(
            _log().redo(principal, change_ref=args.change_ref)
        )
    except PermissionError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    except (ChangeError, RuntimeError, ValueError) as error:
        print(f"Redo failed: {error}", file=sys.stderr)
        return 1
    print(f"Redid {result.change_ref} ({result.target_kind}): {result.detail}")
    return 0


def register_changes_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``hermes changes`` and its sub-actions."""
    parser = subparsers.add_parser(
        "changes",
        help="Review and reverse recorded change events (undo/redo)",
        description=(
            "Inspect the append-only change log and undo/redo changes. "
            "Everything is scoped to --actor by contract C2."
        ),
    )
    sub = parser.add_subparsers(dest="changes_command", required=True)

    listing = sub.add_parser("list", help="List change events visible to --actor")
    listing.add_argument("--actor", required=True, help="Principal user id")
    listing.add_argument(
        "--limit", type=int, default=50, help="Max rows to show (default: 50)"
    )
    listing.add_argument(
        "--active-only",
        action="store_true",
        help="Hide changes that are currently undone",
    )
    listing.set_defaults(func=changes_list_command)

    undo = sub.add_parser("undo", help="Undo a reversible change")
    undo.add_argument("change_ref", help="Change id to undo")
    undo.add_argument("--actor", required=True, help="Principal user id")
    undo.set_defaults(func=changes_undo_command)

    redo = sub.add_parser(
        "redo", help="Redo an undone change (default: the most recently undone)"
    )
    redo.add_argument(
        "change_ref",
        nargs="?",
        default=None,
        help="Change id to redo (omit to redo the top of the redo stack)",
    )
    redo.add_argument("--actor", required=True, help="Principal user id")
    redo.set_defaults(func=changes_redo_command)
