"""``hermes member`` — owner/admin management of additional brain members (C1).

Create a member (GoTrue account → enrolled principal), list members, change a
member's role, reset a temporary password, and deactivate/reactivate login.
Runs on the box as the operator, acting as the enrolled **owner** — the
member-management authority (owner or admin) that :func:`require_member_admin`
requires. Ownership itself is managed separately by ``hermes owner``.

The GoTrue base url comes from ``dashboard.supabase_auth`` / the ``SUPABASE_*``
env vars; the service-role key is read from the environment only (a credential).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from hermes_cli.access import Principal, PrincipalStore, Role
from hermes_cli.datastore import get_store
from hermes_cli.members import (
    ADMIN_UNCONFIGURED_MESSAGE,
    ASSIGNABLE_ROLES,
    MemberError,
    MemberService,
    load_admin_client,
)


def _prod_store() -> PrincipalStore:
    return PrincipalStore(get_store("supabase-app", "prod"))


def _actor() -> Principal:
    """Resolve the enrolled owner to act as (the box operator's authority)."""
    store = _prod_store()
    owner = asyncio.run(store.get_owner())
    if owner is None:
        print(
            "No owner is enrolled; run 'hermes owner init <user_id>' first.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return owner


def _service() -> MemberService:
    admin = load_admin_client()
    if admin is None:
        print(ADMIN_UNCONFIGURED_MESSAGE, file=sys.stderr)
        raise SystemExit(1)
    return MemberService(_prod_store(), admin)


def _prompt_password(provided: str | None) -> str:
    if provided:
        return provided
    first = getpass.getpass("Temporary password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)
    if not first:
        print("Password cannot be empty.", file=sys.stderr)
        raise SystemExit(1)
    return first


def member_list_command(args: argparse.Namespace) -> int:
    """Run ``hermes member list``."""
    try:
        service = _service()
        members = asyncio.run(service.list_members(_actor()))
    except (MemberError, PermissionError, RuntimeError, ValueError) as error:
        print(f"Could not list members: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    if not members:
        print("No members enrolled.")
        return 0
    for m in members:
        status = "active" if m.active else "DEACTIVATED"
        email = m.email or "(no email)"
        print(
            f"{m.role:<6} {m.user_id}  {email}  "
            f"[{status}]  {m.display or ''}".rstrip()
        )
    return 0


def member_add_command(args: argparse.Namespace) -> int:
    """Run ``hermes member add`` — create a GoTrue account + enrol principal."""
    password = _prompt_password(args.password)
    try:
        service = _service()
        role: Role = args.role
        principal = asyncio.run(
            service.create_member(
                _actor(),
                email=args.email,
                password=password,
                display=args.display,
                role=role,
            )
        )
    except (MemberError, PermissionError, RuntimeError, ValueError) as error:
        print(f"Could not create member: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(
        f"Created member {principal.user_id} ({args.email}) as "
        f"{principal.role}. Hand them the temporary password to log in."
    )
    return 0


def member_set_role_command(args: argparse.Namespace) -> int:
    """Run ``hermes member set-role``."""
    try:
        service = _service()
        role: Role = args.role
        principal = asyncio.run(
            service.set_member_role(_actor(), user_id=args.user_id, role=role)
        )
    except (MemberError, PermissionError, RuntimeError, ValueError) as error:
        print(f"Could not change role: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"{principal.user_id} is now {principal.role}.")
    return 0


def member_set_password_command(args: argparse.Namespace) -> int:
    """Run ``hermes member set-password`` — reset a temporary password."""
    password = _prompt_password(args.password)
    try:
        service = _service()
        asyncio.run(
            service.set_member_password(
                _actor(), user_id=args.user_id, password=password
            )
        )
    except (MemberError, PermissionError, RuntimeError, ValueError) as error:
        print(f"Could not reset password: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Reset the password for {args.user_id}.")
    return 0


def member_deactivate_command(args: argparse.Namespace) -> int:
    """Run ``hermes member deactivate`` — block a member's login (ban)."""
    return _set_active(args.user_id, active=False)


def member_activate_command(args: argparse.Namespace) -> int:
    """Run ``hermes member activate`` — restore a member's login (unban)."""
    return _set_active(args.user_id, active=True)


def _set_active(user_id: str, *, active: bool) -> int:
    try:
        service = _service()
        asyncio.run(
            service.set_member_active(_actor(), user_id=user_id, active=active)
        )
    except (MemberError, PermissionError, RuntimeError, ValueError) as error:
        verb = "reactivate" if active else "deactivate"
        print(f"Could not {verb} member: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"{user_id} is now {'active' if active else 'deactivated'}.")
    return 0


def register_member_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``hermes member`` and its sub-actions."""
    parser = subparsers.add_parser(
        "member",
        help="Create and manage additional brain members (owner/admin)",
        description=(
            "Owner/admin management of additional members of the shared "
            "Hermes brain: create a Supabase account and enrol it as a "
            "principal, list members, change roles, reset temporary "
            "passwords, and deactivate/reactivate login. Ownership itself is "
            "managed with 'hermes owner'."
        ),
    )
    member_sub = parser.add_subparsers(dest="member_command", required=True)

    lst = member_sub.add_parser("list", help="List enrolled members")
    lst.set_defaults(func=member_list_command)

    add = member_sub.add_parser(
        "add",
        help="Create a member account and enrol it as a principal",
    )
    add.add_argument("email", help="The new member's email (their login)")
    add.add_argument(
        "--role",
        default="member",
        choices=list(ASSIGNABLE_ROLES),
        help="Role to enrol the member with (default: member)",
    )
    add.add_argument("--display", default="", help="Display name")
    add.add_argument(
        "--password",
        default=None,
        help=(
            "Temporary password (omit to be prompted; prompting avoids the "
            "password landing in shell history)"
        ),
    )
    add.set_defaults(func=member_add_command)

    set_role = member_sub.add_parser(
        "set-role",
        help="Change a member's role (never the owner; never to owner)",
    )
    set_role.add_argument("user_id", help="The member's principal id")
    set_role.add_argument("role", choices=list(ASSIGNABLE_ROLES))
    set_role.set_defaults(func=member_set_role_command)

    set_password = member_sub.add_parser(
        "set-password",
        help="Reset a member's temporary password",
    )
    set_password.add_argument("user_id", help="The member's principal id")
    set_password.add_argument("--password", default=None)
    set_password.set_defaults(func=member_set_password_command)

    deactivate = member_sub.add_parser(
        "deactivate",
        help="Block a member's login without deleting the account",
    )
    deactivate.add_argument("user_id", help="The member's principal id")
    deactivate.set_defaults(func=member_deactivate_command)

    activate = member_sub.add_parser(
        "activate",
        help="Restore a deactivated member's login",
    )
    activate.add_argument("user_id", help="The member's principal id")
    activate.set_defaults(func=member_activate_command)
