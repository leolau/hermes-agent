"""Approval-gated promotion of authored artifacts from dev to prod."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import uuid
from dataclasses import dataclass
from typing import Callable, Literal

from hermes_cli.datastore import (
    ArtifactKind,
    SupabaseAppStore,
    get_store,
    initialize_supabase_app,
)

ApprovalCallback = Callable[..., str]


@dataclass(frozen=True)
class PromotionResult:
    """References emitted by one successful artifact promotion."""

    promotion_ref: str
    approval_ref: str
    change_ref: str
    artifact_kind: ArtifactKind
    artifact_ref: str


def _new_ref(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _decode_definition(value: object) -> object:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _schema_sql(definition: object) -> str:
    if not isinstance(definition, dict):
        raise ValueError("Schema artifact definition must be a JSON object")
    for key, value in definition.items():
        if key == "sql" and isinstance(value, str) and value.strip():
            return value
    raise ValueError("Schema artifact definition must contain non-empty 'sql'")


def _request_approval(
    kind: ArtifactKind,
    ref: str,
    *,
    approval_callback: ApprovalCallback | None,
) -> bool:
    from tools.approval import prompt_dangerous_approval

    choice = prompt_dangerous_approval(
        f"hermes promote {kind}:{ref}",
        "promote an authored artifact from dev to prod",
        allow_permanent=False,
        approval_callback=approval_callback,
    )
    return choice in ("once", "session")


async def promote_artifact(
    store: SupabaseAppStore,
    kind: ArtifactKind,
    ref: str,
    *,
    actor: str,
    approved: bool = False,
    approval_callback: ApprovalCallback | None = None,
) -> PromotionResult:
    """Promote one definition from ``app_dev`` to ``app_prod``.

    Definitions are copied without application data. Schema definitions carry
    a ``sql`` migration that is applied to ``app_prod``. A successful
    transaction records the operator approval, a C5 change-event, and a
    promotion row.
    """
    if kind not in ("tool", "skill", "config", "schema"):
        raise ValueError(f"Unsupported artifact kind: {kind!r}")
    if not ref.strip():
        raise ValueError("Artifact reference cannot be empty")
    if store.mode != "prod":
        raise ValueError("Promotion requires a prod Supabase app store")

    connection = await store.connect()
    try:
        await initialize_supabase_app(connection)
        source = await connection.fetchrow(
            """
            SELECT definition
            FROM app_dev.artifact_definitions
            WHERE kind = $1 AND ref = $2
            """,
            kind,
            ref,
        )
        if source is None:
            raise KeyError(f"Dev artifact not found: {kind}:{ref}")

        if not approved and not _request_approval(
            kind,
            ref,
            approval_callback=approval_callback,
        ):
            raise PermissionError("Promotion approval was denied")

        definition = _decode_definition(source["definition"])
        schema_sql = _schema_sql(definition) if kind == "schema" else None
        canonical_definition = json.dumps(
            definition,
            sort_keys=True,
            separators=(",", ":"),
        )
        approval_ref = _new_ref("apr")
        change_ref = _new_ref("chg")
        promotion_ref = _new_ref("prm")

        async with connection.transaction():
            if schema_sql is not None:
                await connection.execute(schema_sql)

            existing = await connection.fetchval(
                """
                SELECT definition
                FROM app_prod.artifact_definitions
                WHERE kind = $1 AND ref = $2
                """,
                kind,
                ref,
            )
            operation = "replace" if existing is not None else "add"
            inverse_operation: dict[str, object]
            if existing is None:
                inverse_operation = {
                    "op": "remove",
                    "path": f"/artifact_definitions/{kind}/{ref}",
                }
            else:
                inverse_operation = {
                    "op": "replace",
                    "path": f"/artifact_definitions/{kind}/{ref}",
                    "value": _decode_definition(existing),
                }
            operations = [
                {
                    "op": operation,
                    "path": f"/artifact_definitions/{kind}/{ref}",
                    "value": definition,
                }
            ]
            inverse_operations: str | None = json.dumps(
                [inverse_operation],
                sort_keys=True,
            )
            reversible = True
            if schema_sql is not None:
                operations.insert(
                    0,
                    {
                        "op": "execute",
                        "path": "/schemas/app_prod",
                        "value": {"sql": schema_sql},
                    },
                )
                inverse_operations = None
                reversible = False
            await connection.execute(
                """
                INSERT INTO app_prod.approvals
                    (id, action, target_ref, actor, decision)
                VALUES ($1, 'artifact.promote', $2, $3, 'approved')
                """,
                approval_ref,
                f"{kind}:{ref}",
                actor,
            )
            await connection.execute(
                """
                INSERT INTO app_prod.artifact_definitions
                    (kind, ref, definition, updated_at)
                VALUES ($1, $2, $3::jsonb, NOW())
                ON CONFLICT (kind, ref) DO UPDATE SET
                    definition = EXCLUDED.definition,
                    updated_at = EXCLUDED.updated_at
                """,
                kind,
                ref,
                canonical_definition,
            )
            await connection.execute(
                """
                INSERT INTO app_prod.changes
                    (id, actor, mode, target_kind, op, inverse_op, reversible,
                     approval_ref, backup_ref)
                VALUES
                    ($1, $2, 'prod', $3, $4::jsonb, $5::jsonb, $6, $7, NULL)
                """,
                change_ref,
                actor,
                "config" if kind == "config" else "code",
                json.dumps(operations, sort_keys=True),
                inverse_operations,
                reversible,
                approval_ref,
            )
            await connection.execute(
                """
                INSERT INTO app_prod.promotions
                    (id, artifact_kind, artifact_ref, from_mode, to_mode,
                     approval_ref, change_ref, actor)
                VALUES ($1, $2, $3, 'dev', 'prod', $4, $5, $6)
                """,
                promotion_ref,
                kind,
                ref,
                approval_ref,
                change_ref,
                actor,
            )
    finally:
        await connection.close()

    return PromotionResult(
        promotion_ref=promotion_ref,
        approval_ref=approval_ref,
        change_ref=change_ref,
        artifact_kind=kind,
        artifact_ref=ref,
    )


def _parse_artifact(value: str) -> tuple[ArtifactKind, str]:
    raw_kind, separator, ref = value.partition(":")
    if not separator or not ref:
        raise ValueError("Artifact must use KIND:REF syntax")
    if raw_kind == "tool":
        return "tool", ref
    if raw_kind == "skill":
        return "skill", ref
    if raw_kind == "config":
        return "config", ref
    if raw_kind == "schema":
        return "schema", ref
    raise ValueError("KIND must be tool, skill, config, or schema")


def promote_command(args: argparse.Namespace) -> int:
    """Run ``hermes promote``."""
    try:
        kind, ref = _parse_artifact(args.artifact)
        store = get_store("supabase-app", "prod")
        result = asyncio.run(
            promote_artifact(
                store,
                kind,
                ref,
                actor=args.actor,
                approved=args.approve,
            )
        )
    except (KeyError, PermissionError, RuntimeError, ValueError) as error:
        print(f"Promotion failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Promoted {result.artifact_kind}:{result.artifact_ref} "
        f"({result.promotion_ref})"
    )
    return 0


def register_promote_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the approval-gated ``hermes promote`` command."""
    parser = subparsers.add_parser(
        "promote",
        help="Promote an authored artifact from dev to prod",
        description=(
            "Copy a tool, skill, config, or schema definition from app_dev to "
            "app_prod after explicit approval. Raw application data is never copied."
        ),
    )
    parser.add_argument(
        "artifact",
        metavar="KIND:REF",
        help="Artifact to promote (KIND is tool, skill, config, or schema)",
    )
    parser.add_argument(
        "--actor",
        default=getpass.getuser(),
        help="Operator identity written to approval and change records",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Record this invocation as the operator's explicit approval",
    )
    parser.set_defaults(func=promote_command)
