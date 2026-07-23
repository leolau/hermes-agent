"""``hermes owner`` — inspect and transfer the single shared-brain owner (C1)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from hermes_cli.access import PrincipalStore, Role, ensure_app_role
from hermes_cli.datastore import SupabaseAppStore, get_store


def _prod_store() -> PrincipalStore:
    return PrincipalStore(get_store("supabase-app", "prod"))


def _prod_app_store() -> SupabaseAppStore:
    return get_store("supabase-app", "prod")


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


def owner_alias_command(args: argparse.Namespace) -> int:
    """Run ``hermes owner alias`` — map a login subject onto the owner.

    The bootstrap owner was enrolled before the auth provider existed, so its
    ``user_id`` is not the provider subject. This links the owner's real login
    subject (Supabase/GoTrue ``sub``) to the owner principal so their web login
    resolves to the owner without re-keying historical rows.
    """
    try:
        store = _prod_store()

        async def _run() -> str:
            owner = await store.get_owner()
            if owner is None:
                raise ValueError(
                    "No owner is set; run 'hermes owner init <user_id>' first."
                )
            await store.link_alias(args.subject, owner.user_id)
            return owner.user_id

        owner_id = asyncio.run(_run())
    except (KeyError, RuntimeError, ValueError) as error:
        print(f"Owner alias failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Linked login subject {args.subject} -> owner {owner_id}")
    return 0


def owner_db_role_command(args: argparse.Namespace) -> int:
    """Run ``hermes owner db-role`` — provision the least-privilege app role.

    Idempotently creates the non-BYPASSRLS ``hermes_app`` role and grants it
    DML on the prod app schema so request-serving connections can drop to it
    (``SET LOCAL ROLE``) and have Postgres RLS enforce C2 visibility. Run under
    the privileged admin/migration DSN during a maintenance window.
    """
    try:
        store = _prod_app_store()

        async def _run() -> str:
            connection = await store.connect()
            try:
                await ensure_app_role(connection, store.schema)
            finally:
                await connection.close()
            return store.schema

        schema = asyncio.run(_run())
    except (RuntimeError, ValueError) as error:
        print(f"Provisioning the app role failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Ensured least-privilege role 'hermes_app' on schema {schema}")
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

    alias = owner_sub.add_parser(
        "alias",
        help="Map a login subject (Supabase 'sub') onto the current owner",
        description=(
            "Link the owner's auth-provider login subject to the owner "
            "principal so their web login resolves to the owner. Needed only "
            "for a bootstrap owner enrolled before the auth provider existed."
        ),
    )
    alias.add_argument("subject", help="The login subject (e.g. Supabase 'sub')")
    alias.set_defaults(func=owner_alias_command)

    db_role = owner_sub.add_parser(
        "db-role",
        help="Provision the least-privilege, non-BYPASSRLS app DB role",
        description=(
            "Idempotently create the 'hermes_app' role (NOBYPASSRLS) and grant "
            "it DML on the prod app schema, so request-serving connections can "
            "run under it and Postgres RLS enforces per-principal visibility. "
            "Run under the privileged admin DSN during a maintenance window."
        ),
    )
    db_role.set_defaults(func=owner_db_role_command)
