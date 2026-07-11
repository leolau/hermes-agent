"""Mode-aware datastore routing for Hermes core and application state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Mapping, Protocol, overload

from hermes_constants import get_hermes_home
from hermes_cli.config import load_config_readonly

if TYPE_CHECKING:
    import asyncpg

    from hermes_state import SessionDB


StoreKind = Literal["sqlite-core", "supabase-app"]
StoreMode = Literal["dev", "prod"]
ArtifactKind = Literal["tool", "skill", "config", "schema"]

_VALID_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PlatformOrigin(Protocol):
    """Minimal platform-enum contract needed by the mode guard."""

    @property
    def value(self) -> str:
        """Return the platform identifier."""
        ...


class SessionOrigin(Protocol):
    """Minimal session-source contract needed by the mode guard."""

    @property
    def platform(self) -> PlatformOrigin:
        """Return the session's origin platform."""
        ...


@dataclass(frozen=True)
class SQLiteCoreStore:
    """Resolved SQLite core store for one Hermes mode."""

    mode: StoreMode
    path: Path

    def connect(self) -> SessionDB:
        """Open the mode's SQLite session database."""
        from hermes_state import SessionDB

        return SessionDB(db_path=self.path)


@dataclass(frozen=True)
class SupabaseAppStore:
    """Resolved Postgres/Supabase application schema for one Hermes mode."""

    mode: StoreMode
    schema: str
    dsn: str

    async def connect(self) -> asyncpg.Connection:
        """Open a connection whose search path is pinned to this store."""
        if not self.dsn:
            raise RuntimeError(
                "Supabase app datastore is not configured; set "
                "datastore.supabase_app.dsn in config.yaml, preferably as "
                "${DATABASE_URL}."
            )
        if not _VALID_SCHEMA.fullmatch(self.schema):
            raise ValueError(f"Invalid Supabase schema name: {self.schema!r}")

        from tools.lazy_deps import ensure

        ensure("datastore.supabase")

        import asyncpg

        return await asyncpg.connect(
            self.dsn,
            server_settings={"search_path": self.schema},
        )


Datastore = SQLiteCoreStore | SupabaseAppStore


def _platform_name(source: SessionOrigin) -> str:
    return source.platform.value.lower()


def _config_get(
    config: Mapping[str, object],
    *keys: str,
    default: object,
) -> object:
    node: object = config
    for key in keys:
        if not isinstance(node, dict):
            return default
        for candidate_key, candidate_value in node.items():
            if candidate_key == key:
                node = candidate_value
                break
        else:
            return default
    return node


def resolve_mode(
    requested: StoreMode | str | None = None,
    *,
    source: SessionOrigin | None = None,
    config: Mapping[str, object] | None = None,
) -> StoreMode:
    """Resolve a datastore mode, forcing all channel sessions to production.

    Local CLI and dashboard callers may request ``dev`` explicitly. When no
    request is supplied, ``datastore.mode`` is read from ``config.yaml`` and
    defaults to ``prod``. Any non-local ``SessionSource`` is a channel origin
    and resolves to ``prod`` regardless of the requested or configured mode.
    """
    if source is not None and _platform_name(source) not in {
        "local",
        "api_server",
    }:
        return "prod"

    loaded_config = config if config is not None else load_config_readonly()
    candidate = requested
    if candidate is None:
        candidate = _config_get(
            loaded_config,
            "datastore",
            "mode",
            default="prod",
        )
    if candidate == "dev":
        return "dev"
    if candidate == "prod":
        return "prod"
    raise ValueError(f"Invalid datastore mode: {candidate!r}")


@overload
def get_store(
    kind: Literal["sqlite-core"],
    mode: StoreMode | None = None,
    *,
    source: SessionOrigin | None = None,
    config: Mapping[str, object] | None = None,
) -> SQLiteCoreStore: ...


@overload
def get_store(
    kind: Literal["supabase-app"],
    mode: StoreMode | None = None,
    *,
    source: SessionOrigin | None = None,
    config: Mapping[str, object] | None = None,
) -> SupabaseAppStore: ...


def get_store(
    kind: StoreKind,
    mode: StoreMode | None = None,
    *,
    source: SessionOrigin | None = None,
    config: Mapping[str, object] | None = None,
) -> Datastore:
    """Return the typed datastore target for ``kind`` and resolved ``mode``.

    ``sqlite-core`` resolves to ``state.db`` in production and the disposable
    ``state.dev.db`` in development. ``supabase-app`` resolves to the
    ``app_prod`` or ``app_dev`` schema. Mode defaults to ``prod`` and channel
    origins are always forced to ``prod``.
    """
    loaded_config = config if config is not None else load_config_readonly()
    resolved_mode = resolve_mode(mode, source=source, config=loaded_config)

    if kind == "sqlite-core":
        filename = "state.dev.db" if resolved_mode == "dev" else "state.db"
        return SQLiteCoreStore(resolved_mode, get_hermes_home() / filename)
    if kind == "supabase-app":
        base_dsn = _config_get(
            loaded_config,
            "datastore",
            "supabase_app",
            "dsn",
            default="",
        )
        dsn = _config_get(
            loaded_config,
            "datastore",
            "overrides",
            resolved_mode,
            "supabase_app",
            "dsn",
            default="",
        )
        if not dsn:
            dsn = base_dsn
        if not isinstance(dsn, str):
            raise ValueError("Supabase app datastore DSN must be a string")
        schema = "app_dev" if resolved_mode == "dev" else "app_prod"
        return SupabaseAppStore(resolved_mode, schema, dsn)
    raise ValueError(f"Unknown datastore kind: {kind!r}")


async def initialize_supabase_app(connection: asyncpg.Connection) -> None:
    """Create the C3 application schemas and promotion audit tables."""
    await connection.execute(
        """
        CREATE SCHEMA IF NOT EXISTS app_dev;
        CREATE SCHEMA IF NOT EXISTS app_prod;

        CREATE TABLE IF NOT EXISTS app_dev.artifact_definitions (
            kind TEXT NOT NULL,
            ref TEXT NOT NULL,
            definition JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (kind, ref)
        );

        CREATE TABLE IF NOT EXISTS app_prod.artifact_definitions (
            kind TEXT NOT NULL,
            ref TEXT NOT NULL,
            definition JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (kind, ref)
        );

        CREATE TABLE IF NOT EXISTS app_prod.approvals (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            actor TEXT NOT NULL,
            decision TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS app_prod.changes (
            id TEXT PRIMARY KEY,
            ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            actor TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('dev', 'prod')),
            target_kind TEXT NOT NULL,
            CHECK (target_kind IN ('data', 'config', 'code')),
            op JSONB NOT NULL,
            inverse_op JSONB,
            reversible BOOLEAN NOT NULL,
            approval_ref TEXT NOT NULL REFERENCES app_prod.approvals(id),
            backup_ref TEXT
        );

        CREATE TABLE IF NOT EXISTS app_prod.promotions (
            id TEXT PRIMARY KEY,
            artifact_kind TEXT NOT NULL,
            artifact_ref TEXT NOT NULL,
            from_mode TEXT NOT NULL CHECK (from_mode = 'dev'),
            to_mode TEXT NOT NULL CHECK (to_mode = 'prod'),
            approval_ref TEXT NOT NULL REFERENCES app_prod.approvals(id),
            change_ref TEXT NOT NULL REFERENCES app_prod.changes(id),
            actor TEXT NOT NULL,
            ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
