"""``hermes mcp`` subcommand parser.

Extracted from ``hermes_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

import argparse
from typing import Callable

from hermes_cli.subcommands._shared import add_accept_hooks_flag


def build_mcp_parser(subparsers, *, cmd_mcp: Callable) -> None:
    """Attach the ``mcp`` subcommand to ``subparsers``."""
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Manage MCP servers and run Hermes as an MCP server",
        description=(
            "Manage MCP server connections and run Hermes as an MCP server.\n\n"
            "MCP servers provide additional tools via the Model Context Protocol.\n"
            "Use 'hermes mcp add' to connect to a new server, or\n"
            "'hermes mcp serve' to expose Hermes conversations over MCP."
        ),
    )
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")

    mcp_serve_p = mcp_sub.add_parser(
        "serve",
        help="Run Hermes as an MCP server (expose conversations to other agents)",
    )
    mcp_serve_p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging on stderr",
    )
    add_accept_hooks_flag(mcp_serve_p)

    mcp_add_p = mcp_sub.add_parser(
        "add", help="Add an MCP server (discovery-first install)"
    )
    mcp_add_p.add_argument("name", help="Server name (used as config key)")
    mcp_add_p.add_argument("--url", help="HTTP/SSE endpoint URL")
    # dest="mcp_command" so this flag does not clobber the top-level
    # subparser's args.command attribute, which the dispatcher reads to
    # route to cmd_mcp.  Without an explicit dest, argparse derives
    # dest="command" from the flag name and sets it to None when the
    # flag is omitted, causing `hermes mcp add ...` to fall through to
    # interactive chat.
    mcp_add_p.add_argument(
        "--command", dest="mcp_command", help="Stdio command (e.g. npx)"
    )
    mcp_add_p.add_argument(
        "--args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Arguments for stdio command; must be the last option",
    )
    mcp_add_p.add_argument("--auth", choices=["oauth", "header"], help="Auth method")
    mcp_add_p.add_argument("--preset", help="Known MCP preset name")
    mcp_add_p.add_argument(
        "--env",
        nargs="*",
        default=[],
        help="Environment variables for stdio servers (KEY=VALUE)",
    )

    mcp_rm_p = mcp_sub.add_parser("remove", aliases=["rm"], help="Remove an MCP server")
    mcp_rm_p.add_argument("name", help="Server name to remove")

    mcp_sub.add_parser("list", aliases=["ls"], help="List configured MCP servers")

    mcp_test_p = mcp_sub.add_parser("test", help="Test MCP server connection")
    mcp_test_p.add_argument("name", help="Server name to test")

    mcp_cfg_p = mcp_sub.add_parser(
        "configure", aliases=["config"], help="Toggle tool selection"
    )
    mcp_cfg_p.add_argument("name", help="Server name to configure")

    mcp_login_p = mcp_sub.add_parser(
        "login",
        help="Force re-authentication for an OAuth-based MCP server",
    )
    mcp_login_p.add_argument("name", help="Server name to re-authenticate")

    mcp_reauth_p = mcp_sub.add_parser(
        "reauth",
        help="Re-authenticate one OAuth MCP server, or all of them (--all)",
    )
    mcp_reauth_p.add_argument(
        "name", nargs="?", help="Server name to re-authenticate (omit with --all)"
    )
    mcp_reauth_p.add_argument(
        "--all",
        action="store_true",
        help="Re-authenticate every OAuth server in config, one at a time",
    )

    # ── FG-11 agent-comms endpoint registry ───────────────────────────────
    mcp_ep_p = mcp_sub.add_parser(
        "endpoints",
        help="Manage the mode-aware, scoped MCP endpoint registry (FG-11)",
    )
    mcp_ep_sub = mcp_ep_p.add_subparsers(dest="endpoints_action")
    mcp_ep_list = mcp_ep_sub.add_parser(
        "list", aliases=["ls"], help="List endpoints visible to the operator"
    )
    mcp_ep_reg = mcp_ep_sub.add_parser(
        "register", help="Register an in-house or remote MCP endpoint"
    )
    mcp_ep_reg.add_argument("name", help="Endpoint name (unique per mode)")
    mcp_ep_reg.add_argument(
        "--kind",
        choices=["in_house", "remote"],
        default="remote",
        help="Endpoint kind (default: remote)",
    )
    mcp_ep_reg.add_argument("--url", help="HTTP/SSE endpoint URL")
    mcp_ep_reg.add_argument(
        "--command", dest="mcp_command", help="Stdio command (e.g. npx)"
    )
    mcp_ep_reg.add_argument(
        "--args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Arguments for the stdio command; must be the last option",
    )
    mcp_ep_reg.add_argument(
        "--env",
        nargs="*",
        default=[],
        help="Environment variables for stdio servers (KEY=VALUE)",
    )
    mcp_ep_reg.add_argument("--auth", help="Auth hint for http transports")
    mcp_ep_reg.add_argument(
        "--shared",
        action="store_true",
        help="Register as shared (default: private to the operator)",
    )
    for _ep_parser in (mcp_ep_list, mcp_ep_reg):
        _ep_parser.add_argument(
            "--mode",
            choices=["dev", "prod"],
            default=None,
            help="Datastore mode (default: config datastore.mode)",
        )
        _ep_parser.add_argument(
            "--as",
            dest="as_user",
            default=None,
            help="Operate as this principal (default: the enrolled owner)",
        )

    # ── Catalog (Nous-approved MCPs shipped with the repo) ─────────────────
    mcp_sub.add_parser(
        "picker",
        help="Interactive catalog picker (also the default for `hermes mcp`)",
    )
    mcp_sub.add_parser(
        "catalog",
        help="List Nous-approved MCPs available for one-click install",
    )
    mcp_install_p = mcp_sub.add_parser(
        "install",
        help="Install a catalog MCP by name (e.g. `hermes mcp install n8n`)",
    )
    mcp_install_p.add_argument(
        "identifier",
        help="Catalog entry name (or `official/<name>`)",
    )

    add_accept_hooks_flag(mcp_parser)
    mcp_parser.set_defaults(func=cmd_mcp)
