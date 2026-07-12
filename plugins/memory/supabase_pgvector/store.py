"""Live queryable memory tier backed by Supabase Postgres + pgvector.

This is the *live* half of the D2 hybrid memory model. Where the curated tier
(``tools/memory_tool.py``) is a frozen ``MEMORY.md``/``USER.md`` snapshot loaded
once at session start, this store is read and written **mid-turn via tool
calls** — never spliced into the system prompt — so a fact learned this turn is
recallable immediately without disturbing the cached prompt prefix.

Every row is visibility-scoped by contract **C2**
(:mod:`hermes_cli.access`): it carries ``owner_user_id`` + ``visibility``
(``shared`` or ``private:<user_id>``), reads are filtered by
:func:`~hermes_cli.access.scope_filter`, and Postgres **row-level security**
(:func:`~hermes_cli.access.apply_scope_rls`) is the database-level backstop.
All connections are obtained through contract **C3**
(:class:`hermes_cli.datastore.SupabaseAppStore`), so the ``app_dev`` / ``app_prod``
schema follows the resolved mode. Concurrency across many ``(user, task)``
sessions rides Postgres MVCC — each write is its own transaction on its own
connection, so there is no single-writer bottleneck.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from hermes_cli.access import (
    Principal,
    SHARED,
    apply_scope_rls,
    normalize_visibility,
    scope_filter,
)

from .embedding import DEFAULT_DIM, Embedder, get_embedder

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.datastore import SupabaseAppStore


#: Name of the scoped table holding live memory rows (RLS applied to it).
MEMORY_TABLE = "memories"


def _schema_sql(dim: int) -> str:
    return f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS {MEMORY_TABLE} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    text TEXT NOT NULL,
    embedding vector({dim}) NOT NULL,
    source_session TEXT,
    topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used TIMESTAMPTZ,
    uses INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS {MEMORY_TABLE}_embedding_idx
    ON {MEMORY_TABLE} USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS {MEMORY_TABLE}_visibility_idx
    ON {MEMORY_TABLE} (visibility);
CREATE INDEX IF NOT EXISTS {MEMORY_TABLE}_topic_idx
    ON {MEMORY_TABLE} (topic);
"""


@dataclass(frozen=True)
class MemoryRecord:
    """One row of live memory, optionally with a similarity ``score``."""

    id: str
    owner_user_id: str
    visibility: str
    kind: str
    text: str
    topic: Optional[str]
    source_session: Optional[str]
    created_at: Optional[datetime]
    score: Optional[float] = None

    def as_dict(self) -> dict:
        data = {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "kind": self.kind,
            "text": self.text,
            "topic": self.topic,
            "source_session": self.source_session,
        }
        if self.score is not None:
            data["score"] = round(self.score, 6)
        return data


def _encode_vector(vector: List[float]) -> str:
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"


def _decode_vector(text: str) -> List[float]:
    inner = text.strip().lstrip("[").rstrip("]")
    if not inner:
        return []
    return [float(part) for part in inner.split(",")]


def _resolve_visibility(principal: Principal, visibility: Optional[str]) -> str:
    """Map a requested ``shared``/``private`` intent onto a concrete C2 tag.

    ``private`` (without a user) becomes the caller's own
    ``private:<user_id>`` — a principal can only create rows private to
    *itself*. A fully-qualified ``private:<u>`` or ``shared`` is validated and
    passed through.
    """
    if visibility is None or visibility == "private":
        return principal.private_visibility
    return normalize_visibility(visibility)


class PgvectorMemoryStore:
    """Async CRUD + semantic recall over the C2-scoped ``memories`` table.

    The store never opens a raw connection itself — it always routes through
    the injected contract-C3 :class:`SupabaseAppStore`, whose ``mode`` selects
    the ``app_dev`` / ``app_prod`` schema.
    """

    def __init__(
        self,
        store: "SupabaseAppStore",
        *,
        embedder: Optional[Embedder] = None,
    ) -> None:
        self._store = store
        self._embedder = embedder or get_embedder(DEFAULT_DIM)

    @property
    def mode(self) -> str:
        return self._store.mode

    @property
    def dim(self) -> int:
        return self._embedder.dim

    async def _prepare_connection(
        self, connection: "asyncpg.Connection"
    ) -> "asyncpg.Connection":
        """Make ``vector`` usable on ``connection`` (own or caller-injected).

        pgvector may be installed in a schema other than the app schema (a
        standard self-hosted Supabase installs it into ``public``). The
        ``vector`` type, its cast, the ``<=>`` operator, and the ``hnsw``
        ``vector_cosine_ops`` opclass only resolve when that schema is on the
        search path, and the asyncpg codec must be registered against the
        schema the type actually lives in — otherwise the connection fails
        with ``type "vector" does not exist`` / ``operator class ... does not
        exist`` even though the extension is present. This runs on *every*
        connection the store touches — including one handed in by a caller
        such as :class:`GoalManagementService` — so no code path can end up
        with the app schema pinned but the vector schema missing. Idempotent.
        """
        await connection.execute(
            f'CREATE SCHEMA IF NOT EXISTS "{self._store.schema}"'
        )
        await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # Keep the app schema first so scoped tables/RLS still land there.
        vector_schema = await connection.fetchval(
            """
            SELECT n.nspname
            FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname = 'vector'
            """
        ) or self._store.schema
        if vector_schema != self._store.schema:
            await connection.execute(
                "SELECT set_config('search_path', $1, false)",
                f'"{self._store.schema}", "{vector_schema}"',
            )
        await connection.set_type_codec(
            "vector",
            schema=vector_schema,
            encoder=_encode_vector,
            decoder=_decode_vector,
            format="text",
        )
        return connection

    async def _connect(self) -> "asyncpg.Connection":
        connection = await self._store.connect()
        return await self._prepare_connection(connection)

    async def initialize(
        self,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        """Create the ``memories`` table, vector index, and RLS policy.

        Idempotent — safe to call at the start of every session.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            if not own:
                await self._prepare_connection(conn)
            await conn.execute(_schema_sql(self.dim))
            await apply_scope_rls(conn, MEMORY_TABLE)
        finally:
            if own:
                await conn.close()

    async def write(
        self,
        principal: Principal,
        text: str,
        *,
        kind: str = "fact",
        topic: Optional[str] = None,
        visibility: Optional[str] = None,
        source_session: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> MemoryRecord:
        """Persist one memory row owned by ``principal`` and return it.

        ``visibility`` defaults to the caller's own ``private:<user_id>`` tier;
        pass ``"shared"`` to write org-visible knowledge. The row's embedding is
        computed from ``text`` via the configured embedder.
        """
        clean = (text or "").strip()
        if not clean:
            raise ValueError("Cannot write empty memory text")
        resolved_visibility = _resolve_visibility(principal, visibility)
        embedding = self._embedder.embed(clean)

        own = connection is None
        conn = connection or await self._connect()
        try:
            if not own:
                await self._prepare_connection(conn)
            row = await conn.fetchrow(
                f"""
                INSERT INTO {MEMORY_TABLE}
                    (owner_user_id, visibility, kind, text, embedding,
                     topic, source_session)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id, owner_user_id, visibility, kind, text, topic,
                          source_session, created_at
                """,
                principal.user_id,
                resolved_visibility,
                kind,
                clean,
                embedding,
                topic,
                source_session,
            )
            return _row_to_record(row)
        finally:
            if own:
                await conn.close()

    async def get(
        self,
        principal: Principal,
        memory_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[MemoryRecord]:
        """Return one memory when ``principal`` may read it (contract C2)."""
        predicate = scope_filter(principal, start_index=2)
        own = connection is None
        conn = connection or await self._connect()
        try:
            if not own:
                await self._prepare_connection(conn)
            row = await conn.fetchrow(
                f"""
                SELECT id, owner_user_id, visibility, kind, text, topic,
                       source_session, created_at, NULL::float AS score
                FROM {MEMORY_TABLE}
                WHERE id = $1 AND {predicate.sql}
                """,
                memory_id,
                *predicate.params,
            )
            return _row_to_record(row) if row is not None else None
        finally:
            if own:
                await conn.close()

    async def query(
        self,
        principal: Principal,
        query_text: str,
        *,
        top_k: int = 10,
        kind: Optional[str] = None,
        topic: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[MemoryRecord]:
        """Return the ``top_k`` rows most similar to ``query_text``.

        Results are scoped to what ``principal`` may read (contract C2): a
        non-owner sees ``shared`` rows plus its own ``private`` rows; the owner
        sees everything. Ranking is cosine similarity on the embedding.
        """
        top_k = max(1, min(int(top_k), 100))
        embedding = self._embedder.embed(query_text or "")

        params: List[object] = [embedding]
        clauses: List[str] = []
        next_index = 2
        if kind:
            clauses.append(f"kind = ${next_index}")
            params.append(kind)
            next_index += 1
        if topic:
            clauses.append(f"topic = ${next_index}")
            params.append(topic)
            next_index += 1

        predicate = scope_filter(principal, start_index=next_index)
        clauses.append(predicate.sql)
        params.extend(predicate.params)

        where = " AND ".join(clauses)
        sql = f"""
            SELECT id, owner_user_id, visibility, kind, text, topic,
                   source_session, created_at,
                   1 - (embedding <=> $1) AS score
            FROM {MEMORY_TABLE}
            WHERE {where}
            ORDER BY embedding <=> $1
            LIMIT {top_k}
        """

        own = connection is None
        conn = connection or await self._connect()
        try:
            if not own:
                await self._prepare_connection(conn)
            rows = await conn.fetch(sql, *params)
            return [_row_to_record(row) for row in rows]
        finally:
            if own:
                await conn.close()


def _row_to_record(row: "asyncpg.Record") -> MemoryRecord:
    score = row.get("score") if hasattr(row, "get") else None
    return MemoryRecord(
        id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        visibility=str(row["visibility"]),
        kind=str(row["kind"]),
        text=str(row["text"]),
        topic=row["topic"],
        source_session=row["source_session"],
        created_at=row["created_at"],
        score=float(score) if score is not None else None,
    )


__all__ = [
    "MEMORY_TABLE",
    "MemoryRecord",
    "PgvectorMemoryStore",
    "SHARED",
]
