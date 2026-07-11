"""Contract C5 — the append-only change log + undo/redo engine (FG-12).

Every mutating capability (memory writes, config edits, tool/code changes,
promotions, ...) records **one** change event in an append-only log the user
can review and reverse:

    changes(id, ts, actor_user_id, mode, target_kind ∈ {data, config, code},
            op, inverse_op | null, reversible, approval_ref, backup_ref,
            payload)

This module *extends* the ``app_prod.changes`` table already created by C3
(``hermes_cli/datastore.initialize_supabase_app``) rather than introducing a
parallel log — the promotion flow (FG-13) and owner transfer (FG-01) write the
same table. :func:`initialize_changes` adds the FG-12 columns
(``actor_user_id``, ``visibility``, ``payload``, and the undo-state markers)
idempotently, so existing rows and callers keep working.

Undo/redo re-uses the existing engines, per the reuse map:

* **code / files** → ``tools/checkpoint_manager.py`` ``restore`` (git-shadow).
* **config** → inverse-op replay against ``config.yaml``.
* **data** → inverse-op replay (row before/after swap) against the app DB.

A redo stack mirrors undo: an undone change becomes redoable (LIFO). Rows with
``reversible=false`` (e.g. the ERC-721 mint, D6) are recorded but undo refuses
them. Visibility is scoped by contract C2 (``hermes_cli/access``): a member
cannot see or undo another member's ``private:<user>`` change; the owner sees
and undoes all.

Approval is gated by contract C6 (``hermes_cli/consent``): irreversible actions
always require explicit approval; reversible ones may be auto-approved under the
configured consent policy.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import asyncpg

from hermes_cli.access import (
    SHARED,
    Principal,
    can_read,
    normalize_visibility,
    scope_filter,
)
from hermes_cli.consent import (
    ApprovalCallback,
    ConsentDecision,
    ConsentPolicy,
    evaluate_approval,
    load_consent_policy,
)
from hermes_cli.datastore import SupabaseAppStore, get_store

TARGET_KINDS = ("data", "config", "code")

# op["kind"] verbs this engine knows how to apply/reverse. Rows recorded by
# other flows (promotions, owner transfer) use their own op shapes; undo
# refuses anything it does not recognise rather than mangling those rows.
_OP_KINDS = frozenset({"data", "config", "code"})


class ChangeError(RuntimeError):
    """Base class for change-management failures."""


class ChangeNotFound(ChangeError):
    """The requested change id does not exist or is not visible to the caller."""


class IrreversibleChange(ChangeError):
    """Undo was requested for a change recorded as ``reversible=false``."""


class NotUndoable(ChangeError):
    """The change cannot be undone/redone in its current state."""


@dataclass(frozen=True)
class ChangeEvent:
    """A single row of the C5 change log."""

    id: str
    actor_user_id: str | None
    mode: str
    target_kind: str
    op: Any
    inverse_op: Any
    reversible: bool
    approval_ref: str
    backup_ref: str | None
    payload: Any
    visibility: str
    undone: bool

    @classmethod
    def _from_row(cls, row: asyncpg.Record) -> "ChangeEvent":
        return cls(
            id=row["id"],
            actor_user_id=row["actor_user_id"],
            mode=row["mode"],
            target_kind=row["target_kind"],
            op=_load_json(row["op"]),
            inverse_op=_load_json(row["inverse_op"]),
            reversible=row["reversible"],
            approval_ref=row["approval_ref"],
            backup_ref=row["backup_ref"],
            payload=_load_json(row["payload"]),
            visibility=row["visibility"],
            undone=row["undone"],
        )


@dataclass(frozen=True)
class RecordResult:
    change_ref: str
    approval_ref: str
    decision: ConsentDecision


@dataclass(frozen=True)
class UndoResult:
    change_ref: str
    target_kind: str
    detail: str


@dataclass(frozen=True)
class RedoResult:
    change_ref: str
    target_kind: str
    detail: str


_SELECT_COLUMNS = (
    "id, actor_user_id, mode, target_kind, op, inverse_op, reversible, "
    "approval_ref, backup_ref, payload, visibility, undone"
)


async def initialize_changes(connection: asyncpg.Connection) -> None:
    """Ensure the C3 app schema exists and carries the FG-12 change columns.

    Additive + idempotent: the base table is created by
    ``initialize_supabase_app``; here we add the C5 columns FG-12 needs for
    actor attribution, C2 scoping, undo-state, and free-form payload without
    disturbing the existing columns or the promotion/owner-transfer inserts.
    """
    from hermes_cli.datastore import initialize_supabase_app

    await initialize_supabase_app(connection)
    await connection.execute(
        """
        ALTER TABLE app_prod.changes
            ADD COLUMN IF NOT EXISTS actor_user_id TEXT,
            ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'shared',
            ADD COLUMN IF NOT EXISTS payload JSONB,
            ADD COLUMN IF NOT EXISTS undone BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS undone_at TIMESTAMPTZ;
        """
    )


class ChangeLog:
    """Append-only change log with C2-scoped undo/redo (contracts C5 + C6)."""

    def __init__(
        self,
        store: SupabaseAppStore | None = None,
        *,
        config: dict[str, Any] | None = None,
        policy: ConsentPolicy | None = None,
    ) -> None:
        if store is None:
            store = get_store("supabase-app", "prod", config=config)
        if not isinstance(store, SupabaseAppStore):
            raise TypeError("ChangeLog requires a supabase-app store")
        if store.mode != "prod":
            raise ValueError(
                "The change log is a prod-schema audit table; pass a prod store"
            )
        self._store = store
        self._policy = policy or load_consent_policy(config)

    # -- recording ---------------------------------------------------------

    async def record(
        self,
        *,
        actor_user_id: str,
        target_kind: str,
        op: Any,
        inverse_op: Any | None,
        reversible: bool,
        action: str,
        target_ref: str,
        mode: str = "prod",
        visibility: str = SHARED,
        payload: Any | None = None,
        backup_ref: str | None = None,
        approval_callback: ApprovalCallback | None = None,
        approved: bool = False,
        connection: asyncpg.Connection | None = None,
    ) -> RecordResult:
        """Record one C5 change event, gated by the C6 consent policy.

        The consent decision is evaluated first (irreversible ⇒ explicit
        approval required; reversible ⇒ auto-approve when consent allows). A
        denied decision raises ``PermissionError`` and writes nothing. On
        approval an ``approvals`` row and a ``changes`` row are inserted in one
        transaction (approval_ref is a NOT NULL FK on the shared table).
        """
        if target_kind not in TARGET_KINDS:
            raise ValueError(f"Unknown target_kind: {target_kind!r}")
        if reversible and inverse_op is None:
            raise ValueError("A reversible change must record an inverse_op")

        decision: ConsentDecision
        if approved:
            decision = ConsentDecision(approved=True, mode="prompted", reason="preapproved")
        else:
            recent = await self._recent_auto_approvals(connection=connection)
            decision = evaluate_approval(
                self._policy,
                reversible=reversible,
                command=f"hermes changes record {target_kind}:{target_ref}",
                description=action,
                recent_auto_approvals=recent,
                approval_callback=approval_callback,
            )
        if not decision.approved:
            raise PermissionError(f"Change approval was denied ({decision.reason})")

        change_ref = f"chg_{uuid.uuid4().hex}"
        approval_ref = f"apr_{uuid.uuid4().hex}"
        norm_visibility = normalize_visibility(visibility)
        approval_decision = "approved" if decision.mode != "auto" else "auto"

        async def _write(conn: asyncpg.Connection) -> None:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO app_prod.approvals
                        (id, action, target_ref, actor, decision)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    approval_ref,
                    action,
                    f"{target_kind}:{target_ref}",
                    actor_user_id,
                    approval_decision,
                )
                await conn.execute(
                    """
                    INSERT INTO app_prod.changes
                        (id, actor, actor_user_id, mode, target_kind, op,
                         inverse_op, reversible, approval_ref, backup_ref,
                         payload, visibility)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9,
                            $10, $11::jsonb, $12)
                    """,
                    change_ref,
                    actor_user_id,
                    actor_user_id,
                    mode,
                    target_kind,
                    _dump_json(op),
                    _dump_json(inverse_op) if inverse_op is not None else None,
                    reversible,
                    approval_ref,
                    backup_ref,
                    _dump_json(payload) if payload is not None else None,
                    norm_visibility,
                )

        await self._with_connection(_write, connection)
        return RecordResult(change_ref, approval_ref, decision)

    # -- reading -----------------------------------------------------------

    async def list_changes(
        self,
        principal: Principal,
        *,
        include_undone: bool = True,
        limit: int = 100,
        connection: asyncpg.Connection | None = None,
    ) -> list[ChangeEvent]:
        """List change events visible to ``principal`` (C2-scoped), newest first."""
        predicate = scope_filter(principal, column="visibility", start_index=1)
        where = [predicate.sql]
        if not include_undone:
            where.append("undone = FALSE")
        params: list[object] = list(predicate.params)
        limit_placeholder = f"${len(params) + 1}"
        params.append(limit)
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM app_prod.changes "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY ts DESC LIMIT {limit_placeholder}"
        )

        async def _read(conn: asyncpg.Connection) -> list[ChangeEvent]:
            rows = await conn.fetch(sql, *params)
            return [ChangeEvent._from_row(r) for r in rows]

        return await self._with_connection(_read, connection)

    async def get(
        self,
        change_ref: str,
        principal: Principal,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> ChangeEvent:
        """Fetch a single change, enforcing C2 visibility for ``principal``."""

        async def _read(conn: asyncpg.Connection) -> ChangeEvent:
            return await self._load_visible(conn, change_ref, principal)

        return await self._with_connection(_read, connection)

    # -- undo / redo -------------------------------------------------------

    async def undo(
        self,
        change_ref: str,
        principal: Principal,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> UndoResult:
        """Reverse a change: code via checkpoint restore, config/data via inverse-op.

        Refuses irreversible changes (D6), changes not visible to ``principal``
        (C2), and changes already undone.
        """

        async def _run(conn: asyncpg.Connection) -> UndoResult:
            event = await self._load_visible(conn, change_ref, principal)
            if not event.reversible:
                raise IrreversibleChange(
                    f"{change_ref} is irreversible and cannot be undone"
                )
            if event.undone:
                raise NotUndoable(f"{change_ref} is already undone")
            if event.inverse_op is None:
                raise NotUndoable(f"{change_ref} has no inverse operation")
            detail = await _apply(event.inverse_op, conn=conn)
            await conn.execute(
                "UPDATE app_prod.changes SET undone = TRUE, undone_at = NOW() "
                "WHERE id = $1",
                change_ref,
            )
            return UndoResult(change_ref, event.target_kind, detail)

        return await self._with_connection(_run, connection)

    async def redo(
        self,
        principal: Principal,
        *,
        change_ref: str | None = None,
        connection: asyncpg.Connection | None = None,
    ) -> RedoResult:
        """Re-apply an undone change. With no id, redo the most recently undone.

        Only changes visible to ``principal`` (C2) are eligible.
        """

        async def _run(conn: asyncpg.Connection) -> RedoResult:
            if change_ref is None:
                target = await self._top_of_redo_stack(conn, principal)
                if target is None:
                    raise NotUndoable("The redo stack is empty")
            else:
                target = await self._load_visible(conn, change_ref, principal)
                if not target.undone:
                    raise NotUndoable(f"{target.id} is not undone; nothing to redo")
            detail = await _apply(target.op, conn=conn)
            await conn.execute(
                "UPDATE app_prod.changes SET undone = FALSE, undone_at = NULL "
                "WHERE id = $1",
                target.id,
            )
            return RedoResult(target.id, target.target_kind, detail)

        return await self._with_connection(_run, connection)

    # -- internals ---------------------------------------------------------

    async def _load_visible(
        self,
        conn: asyncpg.Connection,
        change_ref: str,
        principal: Principal,
    ) -> ChangeEvent:
        row = await conn.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM app_prod.changes WHERE id = $1",
            change_ref,
        )
        if row is None:
            raise ChangeNotFound(f"No such change: {change_ref}")
        event = ChangeEvent._from_row(row)
        if not can_read(principal, event.visibility):
            # Same surface as "not found" would be nicer for privacy, but a
            # distinct, explicit signal is more useful for undo callers.
            raise PermissionError(
                f"{principal.user_id} may not access change {change_ref}"
            )
        return event

    async def _top_of_redo_stack(
        self, conn: asyncpg.Connection, principal: Principal
    ) -> ChangeEvent | None:
        predicate = scope_filter(principal, column="visibility", start_index=1)
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM app_prod.changes "
            f"WHERE undone = TRUE AND ({predicate.sql}) "
            f"ORDER BY undone_at DESC NULLS LAST LIMIT 1"
        )
        row = await conn.fetchrow(sql, *predicate.params)
        return ChangeEvent._from_row(row) if row is not None else None

    async def _recent_auto_approvals(
        self, *, connection: asyncpg.Connection | None
    ) -> int:
        window = self._policy.rate_limit_window_seconds

        async def _count(conn: asyncpg.Connection) -> int:
            value = await conn.fetchval(
                """
                SELECT COUNT(*) FROM app_prod.approvals
                WHERE decision = 'auto'
                  AND created_at >= NOW() - ($1::text || ' seconds')::interval
                """,
                str(window),
            )
            return int(value or 0)

        return await self._with_connection(_count, connection)

    async def _with_connection(self, fn, connection):
        if connection is not None:
            return await fn(connection)
        conn = await self._store.connect()
        try:
            return await fn(conn)
        finally:
            await conn.close()


# ---------------------------------------------------------------------------
# Operation application (inverse-op replay + checkpoint restore)
# ---------------------------------------------------------------------------


async def _apply(op: Any, *, conn: asyncpg.Connection) -> str:
    """Apply one operation (forward or inverse — they share a shape).

    Dispatch on ``op['kind']``. Config/data ops replay declaratively; code ops
    delegate to the checkpoint restore engine. Unknown shapes are refused so we
    never mangle rows recorded by other flows.
    """
    if not isinstance(op, Mapping):
        raise NotUndoable("Operation is not a recognised object")
    kind = op.get("kind")
    if kind not in _OP_KINDS:
        raise NotUndoable(f"Cannot reverse operation of kind {kind!r}")
    if kind == "config":
        return _apply_config_op(op)
    if kind == "code":
        return _apply_code_op(op)
    return await _apply_data_op(op, conn=conn)


def _apply_config_op(op: Mapping[str, Any]) -> str:
    """Set (or remove) a dotted config.yaml path to the operation's target state."""
    from hermes_cli.config import read_raw_config, save_config

    path = op.get("path")
    if not isinstance(path, str) or not path:
        raise NotUndoable("config op missing a 'path'")
    cfg = read_raw_config()
    if op.get("present", True):
        _set_dotted(cfg, path, op.get("value"))
        detail = f"config {path} set"
    else:
        _remove_dotted(cfg, path)
        detail = f"config {path} removed"
    save_config(cfg, strip_defaults=False)
    return detail


def _apply_code_op(op: Mapping[str, Any]) -> str:
    """Restore a working directory to the operation's target checkpoint."""
    from tools.checkpoint_manager import CheckpointManager

    working_dir = op.get("working_dir")
    commit = op.get("commit")
    if not isinstance(working_dir, str) or not isinstance(commit, str):
        raise NotUndoable("code op requires 'working_dir' and 'commit'")
    file_path = op.get("file_path")
    manager = CheckpointManager()
    if isinstance(file_path, str) and file_path:
        result = manager.restore(working_dir, commit, file_path)
    else:
        result = manager.restore(working_dir, commit)
    if not result.get("success"):
        raise NotUndoable(f"checkpoint restore failed: {result.get('error')}")
    return f"code restored to {commit[:12]}"


async def _apply_data_op(op: Mapping[str, Any], *, conn: asyncpg.Connection) -> str:
    """Transition a row to the operation's target state (null = row absent).

    ``{"kind":"data","table":T,"pk":{...},"state":{...}|null}`` — ``state`` is
    the full desired row (``None`` means the row should not exist). This one
    verb expresses insert (state set, was absent), update (state set), and
    delete (state null) symmetrically, so forward and inverse ops are just each
    other's ``state``.
    """
    table = op.get("table")
    pk = op.get("pk")
    if not isinstance(table, str) or not table:
        raise NotUndoable("data op missing 'table'")
    if not isinstance(pk, Mapping) or not pk:
        raise NotUndoable("data op missing 'pk'")
    _assert_identifier(table)
    for key in pk:
        _assert_identifier(str(key))
    state = op.get("state")

    if state is None:
        where, params = _eq_clause(dict(pk), start=1)
        await conn.execute(f"DELETE FROM {table} WHERE {where}", *params)
        return f"data {table} row deleted"

    if not isinstance(state, Mapping):
        raise NotUndoable("data op 'state' must be an object or null")
    for key in state:
        _assert_identifier(str(key))
    columns = list(state.keys())
    placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
    col_list = ", ".join(columns)
    pk_cols = list(pk.keys())
    update_cols = [c for c in columns if c not in pk]
    if update_cols:
        assignments = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        conflict = f"ON CONFLICT ({', '.join(pk_cols)}) DO UPDATE SET {assignments}"
    else:
        conflict = f"ON CONFLICT ({', '.join(pk_cols)}) DO NOTHING"
    await conn.execute(
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) {conflict}",
        *[state[c] for c in columns],
    )
    return f"data {table} row upserted"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _dump_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _load_json(value: Any) -> Any:
    if value is None or not isinstance(value, (str, bytes, bytearray)):
        return value
    return json.loads(value)


def _set_dotted(cfg: dict, path: str, value: Any) -> None:
    keys = path.split(".")
    node = cfg
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value


def _remove_dotted(cfg: dict, path: str) -> None:
    keys = path.split(".")
    node = cfg
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            return
        node = child
    node.pop(keys[-1], None)


def _assert_identifier(name: str) -> None:
    """Guard against SQL injection through table/column names in data ops."""
    if not name or not all(c.isalnum() or c == "_" for c in name):
        raise NotUndoable(f"Unsafe SQL identifier in data op: {name!r}")


def _eq_clause(fields: Mapping[str, Any], *, start: int) -> tuple[str, list[object]]:
    parts: list[str] = []
    params: list[object] = []
    index = start
    for key, value in fields.items():
        _assert_identifier(str(key))
        parts.append(f"{key} = ${index}")
        params.append(value)
        index += 1
    return " AND ".join(parts), params


def config_op(path: str, *, before: Any, after: Any, before_present: bool = True,
              after_present: bool = True) -> tuple[dict, dict]:
    """Build a (forward, inverse) config op pair for a dotted path.

    ``forward`` moves the path to ``after``; ``inverse`` moves it back to
    ``before``. Use ``*_present=False`` when the value was/should be absent.
    """
    forward = {"kind": "config", "path": path, "present": after_present, "value": after}
    inverse = {"kind": "config", "path": path, "present": before_present, "value": before}
    return forward, inverse


def data_op(table: str, pk: Mapping[str, Any], *, before: Any, after: Any) -> tuple[dict, dict]:
    """Build a (forward, inverse) data op pair.

    ``before``/``after`` are the full row state (``None`` = row absent) before
    and after the change.
    """
    forward = {"kind": "data", "table": table, "pk": dict(pk), "state": after}
    inverse = {"kind": "data", "table": table, "pk": dict(pk), "state": before}
    return forward, inverse


def code_op(working_dir: str, *, commit_before: str, commit_after: str,
            file_path: str | None = None) -> tuple[dict, dict]:
    """Build a (forward, inverse) code op pair referencing checkpoint commits.

    ``forward`` (redo) restores to ``commit_after``; ``inverse`` (undo) restores
    to ``commit_before``.
    """
    forward = {"kind": "code", "working_dir": working_dir, "commit": commit_after,
               "file_path": file_path}
    inverse = {"kind": "code", "working_dir": working_dir, "commit": commit_before,
               "file_path": file_path}
    return forward, inverse


__all__: Sequence[str] = (
    "TARGET_KINDS",
    "ChangeEvent",
    "ChangeLog",
    "ChangeError",
    "ChangeNotFound",
    "IrreversibleChange",
    "NotUndoable",
    "RecordResult",
    "UndoResult",
    "RedoResult",
    "initialize_changes",
    "config_op",
    "data_op",
    "code_op",
)
