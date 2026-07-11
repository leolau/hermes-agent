"""Supabase + pgvector memory provider — the live tier of hybrid memory (D2).

This provider is the **live, queryable** half of Hermes' hybrid memory model.
The curated tier (``tools/memory_tool.py``) stays exactly as-is: a frozen
``MEMORY.md`` / ``USER.md`` snapshot loaded once at session start, so the system
prompt prefix is byte-stable for the life of a conversation and prompt caching
is preserved. This provider adds a second tier the agent reaches **only through
tool calls mid-turn** — ``memory_write`` to persist a volatile/coordination fact
or embedding, ``memory_query`` to recall by meaning. Their results come back as
ordinary *appended tool-result messages*; nothing here is ever spliced into the
system prompt, so a mid-session write never invalidates the cache (proven by the
cache-safety test).

Rows are visibility-scoped by contract **C2** (owner + ``shared`` /
``private:<user_id>``, filtered by principal, RLS as the DB backstop) and routed
through contract **C3** (``app_dev`` / ``app_prod`` Postgres schema by mode).
Many concurrent ``(user, task)`` sessions can write at once because each write
is an independent transaction under Postgres MVCC — no SQLite single-writer
bottleneck.

Activate via ``config.yaml``::

    memory:
      provider: supabase_pgvector

The Postgres DSN is read from contract C3's ``datastore.supabase_app.dsn`` (the
one secret; ``.env`` holds it as ``${DATABASE_URL}``). No new ``HERMES_*`` env
vars are introduced for behaviour.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from hermes_cli.access import Principal

from .embedding import DEFAULT_DIM
from .store import PgvectorMemoryStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas (frozen at session start; results are appended messages)
# ---------------------------------------------------------------------------

QUERY_SCHEMA = {
    "name": "memory_query",
    "description": (
        "Search the live, shared memory store by meaning and get back the most "
        "relevant facts. Use this mid-turn BEFORE answering anything that may "
        "depend on volatile or coordination state, on facts other sessions may "
        "have written, or on what you recorded earlier this conversation — it "
        "reflects writes made moments ago, unlike the frozen memory snapshot in "
        "your system prompt. Results are scoped to what you're allowed to see."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to recall, in natural language.",
            },
            "top_k": {
                "type": "integer",
                "description": "Max results to return (default 10, max 100).",
            },
            "topic": {
                "type": "string",
                "description": "Optional exact-match topic filter.",
            },
            "kind": {
                "type": "string",
                "description": "Optional exact-match kind filter (e.g. 'fact').",
            },
        },
        "required": ["query"],
    },
}

WRITE_SCHEMA = {
    "name": "memory_write",
    "description": (
        "Store a fact in the live, shared memory store so it (and other "
        "sessions) can recall it later via memory_query. Use for volatile or "
        "coordination state and anything worth remembering mid-conversation. "
        "By default the fact is PRIVATE to the current user; pass "
        "visibility='shared' only for org-wide knowledge everyone should see. "
        "This does NOT edit your system prompt — durable, curated facts still go "
        "through the separate 'memory' snapshot tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact to store, stated plainly.",
            },
            "visibility": {
                "type": "string",
                "enum": ["private", "shared"],
                "description": "'private' (default, only this user) or 'shared'.",
            },
            "topic": {
                "type": "string",
                "description": "Optional short topic label for later filtering.",
            },
            "kind": {
                "type": "string",
                "description": "Optional category (default 'fact').",
            },
        },
        "required": ["content"],
    },
}


class SupabasePgvectorMemoryProvider(MemoryProvider):
    """Live pgvector memory tier registered through the MemoryProvider ABC."""

    def __init__(self) -> None:
        self._store: Optional[PgvectorMemoryStore] = None
        self._principal: Optional[Principal] = None
        self._session_id = ""
        self._init_error = ""
        self._dim = DEFAULT_DIM

    @property
    def name(self) -> str:
        return "supabase_pgvector"

    # -- Availability / config ----------------------------------------------

    def _resolve_dsn(self, config: Optional[dict] = None) -> str:
        try:
            from hermes_cli.datastore import get_store

            store = get_store("supabase-app", config=config)
            return store.dsn
        except Exception:
            return ""

    def is_available(self) -> bool:
        """True when a Supabase DSN (contract C3) is configured.

        Pure config check — no network — as the ABC requires.
        """
        return bool(self._resolve_dsn())

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "dsn",
                "description": (
                    "Supabase/Postgres DSN for the app datastore (contract C3). "
                    "Set datastore.supabase_app.dsn in config.yaml, preferably "
                    "as ${DATABASE_URL}."
                ),
                "secret": True,
                "required": True,
                "env_var": "DATABASE_URL",
            },
        ]

    # -- Lifecycle -----------------------------------------------------------

    def _resolve_principal(self, kwargs: Dict[str, Any]) -> Principal:
        """Derive the C2 principal for this session from init kwargs.

        Prefers an explicit ``principal_user_id`` / ``principal_role`` (the seam
        FG-01's ``resolve_principal`` can populate). Falls back to the gateway
        ``user_id`` as a ``member`` (its own private tier + shared). A local
        session with no user identity maps to the single ``owner`` — the
        one-brain personal-deployment default where the owner sees everything.
        """
        explicit_id = kwargs.get("principal_user_id")
        explicit_role = kwargs.get("principal_role")
        if explicit_id:
            return Principal(
                user_id=str(explicit_id),
                display=str(kwargs.get("user_name") or explicit_id),
                role=explicit_role if explicit_role in ("owner", "admin", "member", "viewer") else "member",
            )

        gateway_user = kwargs.get("user_id")
        if gateway_user:
            return Principal(
                user_id=str(gateway_user),
                display=str(kwargs.get("user_name") or gateway_user),
                role="member",
            )
        return Principal(user_id="owner", display="owner", role="owner")

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id or ""
        self._principal = self._resolve_principal(kwargs)
        try:
            from tools.lazy_deps import ensure

            ensure("datastore.supabase", prompt=False)
        except Exception:
            pass
        try:
            from hermes_cli.datastore import get_store

            store = get_store("supabase-app")
            self._store = PgvectorMemoryStore(store)
            self._dim = self._store.dim
            self._run_async(self._store.initialize())
        except Exception as exc:  # pragma: no cover - env/config dependent
            self._init_error = str(exc)
            self._store = None
            logger.warning("supabase_pgvector initialize failed: %s", exc)

    def system_prompt_block(self) -> str:
        """STATIC provider info only — never dynamic recall (cache-safe).

        Nothing here changes across turns, so including it keeps the system
        prompt byte-stable for the whole conversation. Recall arrives strictly
        via ``memory_query`` tool results, which are appended messages.
        """
        return (
            "# Live Memory (Supabase + pgvector)\n"
            "Beyond the frozen memory snapshot above, you have a LIVE, shared "
            "memory store you reach only through tool calls. It reflects writes "
            "made moments ago (including by other sessions). Call memory_query "
            "before answering anything that may depend on volatile/coordination "
            "state or on facts recorded earlier this conversation, and "
            "memory_write to save a new fact worth recalling later. Reads and "
            "writes never change this system prompt."
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [QUERY_SCHEMA, WRITE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if self._store is None or self._principal is None:
            return json.dumps(
                {
                    "error": (
                        "Live memory store is unavailable"
                        + (f": {self._init_error}" if self._init_error else "")
                    )
                }
            )
        try:
            if tool_name == "memory_query":
                return self._handle_query(args)
            if tool_name == "memory_write":
                return self._handle_write(args)
        except Exception as exc:
            logger.debug("supabase_pgvector tool %s failed: %s", tool_name, exc)
            return json.dumps({"error": f"{tool_name} failed: {exc}"})
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def _handle_query(self, args: Dict[str, Any]) -> str:
        assert self._store is not None and self._principal is not None
        query = str(args.get("query", "")).strip()
        if not query:
            return json.dumps({"error": "memory_query requires a 'query'"})
        results = self._run_async(
            self._store.query(
                self._principal,
                query,
                top_k=int(args.get("top_k", 10) or 10),
                kind=args.get("kind") or None,
                topic=args.get("topic") or None,
            )
        )
        return json.dumps(
            {"results": [record.as_dict() for record in results]},
            ensure_ascii=False,
        )

    def _handle_write(self, args: Dict[str, Any]) -> str:
        assert self._store is not None and self._principal is not None
        content = str(args.get("content", "")).strip()
        if not content:
            return json.dumps({"error": "memory_write requires 'content'"})
        record = self._run_async(
            self._store.write(
                self._principal,
                content,
                kind=str(args.get("kind") or "fact"),
                topic=args.get("topic") or None,
                visibility=args.get("visibility") or None,
                source_session=self._session_id or None,
            )
        )
        return json.dumps(
            {"stored": record.as_dict()},
            ensure_ascii=False,
        )

    # -- Async bridge --------------------------------------------------------

    @staticmethod
    def _run_async(coro):
        """Run a coroutine to completion on a private loop and return its result.

        The agent loop is synchronous; asyncpg is async. Running on a fresh loop
        in a worker thread avoids any 'event loop already running' clash if this
        provider is ever driven from an async context.
        """
        import asyncio

        box: Dict[str, Any] = {}

        def runner() -> None:
            try:
                box["result"] = asyncio.run(coro)
            except BaseException as exc:  # noqa: BLE001 - re-raised on caller thread
                box["error"] = exc

        thread = threading.Thread(target=runner, name="pgvector-memory")
        thread.start()
        thread.join()
        if "error" in box:
            raise box["error"]
        return box.get("result")


def register(ctx) -> None:
    """Plugin entry point: register the provider with the memory manager."""
    ctx.register_memory_provider(SupabasePgvectorMemoryProvider())


__all__ = ["SupabasePgvectorMemoryProvider", "register"]
