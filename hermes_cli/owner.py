"""``hermes owner`` — inspect and transfer the single shared-brain owner (C1)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from hermes_cli.access import PrincipalStore, Role
from hermes_cli.datastore import get_store


def _prod_store() -> PrincipalStore:
    return PrincipalStore(get_store("supabase-app", "prod"))


def owner_show_command(args: argparse.Namespace) -> int:
    """Run ``hermes owner show``."""
    try:
        store = _prod_store()
        owner = asyncio.run(store.get_owner())
    except (RuntimeError, ValueError) as error:
        print(f"Could not read owner: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    if owner is None:
        print("No owner is set. Enroll one with 'hermes owner init <user_id>'.")
        return 0
    channels = ", ".join(owner.channels) if owner.channels else "(none)"
    print(f"Owner: {owner.user_id} ({owner.display or 'no display name'})")
    print(f"Channels: {channels}")
    return 0


def owner_init_command(args: argparse.Namespace) -> int:
    """Run ``hermes owner init`` — bootstrap the first owner."""
    try:
        store = _prod_store()
        existing = asyncio.run(store.get_owner())
        if existing is not None:
            print(
                f"Owner already set ({existing.user_id}); use "
                "'hermes owner transfer' to change it.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        principal = asyncio.run(
            store.enroll(args.user_id, display=args.display, role="owner")
        )
    except (KeyError, RuntimeError, ValueError) as error:
        print(f"Owner init failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Owner set to {principal.user_id}")
    return 0


def owner_transfer_command(args: argparse.Namespace) -> int:
    """Run ``hermes owner transfer`` — approval-gated ownership handoff."""
    try:
        store = _prod_store()
        demote_to: Role = args.demote_to
        result = asyncio.run(
            store.transfer_owner(
                args.user_id,
                actor=args.actor,
                approved=args.approve,
                demote_to=demote_to,
            )
        )
    except (KeyError, PermissionError, RuntimeError, ValueError) as error:
        print(f"Ownership transfer failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(
        f"Ownership transferred {result.from_user_id} -> {result.to_user_id} "
        f"({result.change_ref})"
    )
    return 0


def register_owner_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``hermes owner`` and its sub-actions."""
    parser = subparsers.add_parser(
        "owner",
        help="Inspect or transfer the single shared-brain owner",
        description=(
            "Manage the single transferable owner of the shared Hermes brain. "
            "Exactly one principal holds the owner role; ownership transfer is "
            "approval-gated and recorded as a change-event."
        ),
    )
    owner_sub = parser.add_subparsers(dest="owner_command", required=True)

    show = owner_sub.add_parser("show", help="Show the current owner")
    show.set_defaults(func=owner_show_command)

    init = owner_sub.add_parser(
        "init",
        help="Bootstrap the first owner (fails if one already exists)",
    )
    init.add_argument("user_id", help="System user id (GoTrue subject) to make owner")
    init.add_argument("--display", default="", help="Display name for the owner")
    init.set_defaults(func=owner_init_command)

    transfer = owner_sub.add_parser(
        "transfer",
        help="Transfer ownership to another enrolled principal",
        description=(
            "Move the single owner role to an already-enrolled principal. The "
            "current owner must approve; the outgoing owner is demoted and the "
            "target promoted atomically so exactly one owner always exists."
        ),
    )
    transfer.add_argument("user_id", help="Target principal's system user id")
    transfer.add_argument(
        "--actor",
        default=getpass.getuser(),
        help="Operator identity written to approval and change records",
    )
    transfer.add_argument(
        "--approve",
        action="store_true",
        help="Record this invocation as the current owner's explicit approval",
    )
    transfer.add_argument(
        "--demote-to",
        dest="demote_to",
        default="admin",
        choices=["admin", "member", "viewer"],
        help="Role assigned to the outgoing owner (default: admin)",
    )
    transfer.set_defaults(func=owner_transfer_command)
