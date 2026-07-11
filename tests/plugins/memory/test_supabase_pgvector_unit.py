"""Unit + cache-safety coverage for the FG-05 live pgvector memory tier.

These tests run WITHOUT Postgres or Docker — they exercise the embedding
math, principal resolution, C2 scope predicate, and (critically) the
**prompt-cache safety invariant**: a mid-session ``memory_write`` must not
change the provider's system-prompt block or its tool schemas, because those
are what get frozen into the cached prompt prefix at session start.

The DB-backed round-trip / concurrency / RLS negative-access tests live in
``test_supabase_pgvector_e2e.py`` (throwaway pgvector Postgres).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

import pytest

from hermes_cli.access import Principal, private, scope_filter
from plugins.memory.supabase_pgvector import SupabasePgvectorMemoryProvider
from plugins.memory.supabase_pgvector.embedding import HashingEmbedder
from plugins.memory.supabase_pgvector.store import MemoryRecord, _resolve_visibility


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def test_embedding_is_deterministic_and_unit_norm() -> None:
    embedder = HashingEmbedder(dim=128)
    a = embedder.embed("green tea in the morning")
    b = embedder.embed("green tea in the morning")
    assert a == b  # deterministic across calls
    assert len(a) == 128
    norm = sum(component * component for component in a) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_embedding_empty_text_is_zero_vector() -> None:
    embedder = HashingEmbedder(dim=32)
    assert embedder.embed("") == [0.0] * 32
    assert embedder.embed("   ") == [0.0] * 32


def _cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def test_shared_vocabulary_is_closer_than_disjoint() -> None:
    embedder = HashingEmbedder(dim=256)
    query = embedder.embed("what beverage does alice drink")
    related = embedder.embed("alice drinks green tea as her beverage")
    unrelated = embedder.embed("quarterly revenue projections spreadsheet")
    assert _cosine(query, related) > _cosine(query, unrelated)


# ---------------------------------------------------------------------------
# C2 scoping helpers used by the store
# ---------------------------------------------------------------------------

def test_resolve_visibility_defaults_to_callers_private() -> None:
    alice = Principal(user_id="alice", display="a", role="member")
    assert _resolve_visibility(alice, None) == private("alice")
    assert _resolve_visibility(alice, "private") == private("alice")
    assert _resolve_visibility(alice, "shared") == "shared"


def test_scope_filter_owner_bypasses_non_owner_is_constrained() -> None:
    owner = Principal(user_id="root", display="r", role="owner")
    member = Principal(user_id="alice", display="a", role="member")
    assert scope_filter(owner).sql == "TRUE"
    member_pred = scope_filter(member, start_index=2)
    assert member_pred.params == (private("alice"),)
    assert "$2" in member_pred.sql and "shared" in member_pred.sql


# ---------------------------------------------------------------------------
# Cache safety — the headline FG-05 requirement
# ---------------------------------------------------------------------------

@dataclass
class _FakeStore:
    """In-memory stand-in for PgvectorMemoryStore (no Postgres)."""

    rows: List[MemoryRecord]
    dim: int = 256
    mode: str = "prod"

    async def initialize(self, *, connection=None) -> None:  # noqa: D401
        return None

    async def write(self, principal, text, *, kind="fact", topic=None,
                    visibility=None, source_session=None, connection=None):
        vis = _resolve_visibility(principal, visibility)
        record = MemoryRecord(
            id=f"id-{len(self.rows)}",
            owner_user_id=principal.user_id,
            visibility=vis,
            kind=kind,
            text=text,
            topic=topic,
            source_session=source_session,
            created_at=None,
        )
        self.rows.append(record)
        return record

    async def query(self, principal, query_text, *, top_k=10, kind=None,
                    topic=None, connection=None):
        return list(self.rows)[:top_k]


@dataclass(frozen=True)
class _FakeOutcome:
    action: str


class _FakeDiscovery:
    def __init__(self, proposal_sink) -> None:
        self.proposal_sink = proposal_sink
        self.origins: list[str] = []

    async def observe_prompt(
        self,
        principal,
        message,
        *,
        source_session=None,
        origin="user",
    ):
        self.origins.append(origin)
        if origin == "user":
            self.proposal_sink(f"Proposal for: {message}")
        return _FakeOutcome(action="proposal_denied")


def _provider_with_fake_store() -> SupabasePgvectorMemoryProvider:
    provider = SupabasePgvectorMemoryProvider()
    provider._store = _FakeStore(rows=[])  # type: ignore[assignment]
    provider._principal = Principal(user_id="alice", display="a", role="member")
    provider._session_id = "sess-1"
    return provider


def test_system_prompt_block_is_byte_stable_across_a_mid_session_write() -> None:
    provider = _provider_with_fake_store()

    prompt_before = provider.system_prompt_block()
    schemas_before = json.dumps(provider.get_tool_schemas(), sort_keys=True)

    # A brand-new fact learned mid-conversation.
    result = provider.handle_tool_call(
        "memory_write",
        {"content": "alice just said she now prefers oat milk", "visibility": "private"},
    )
    stored = json.loads(result)["stored"]
    assert stored["text"].startswith("alice just said")

    prompt_after = provider.system_prompt_block()
    schemas_after = json.dumps(provider.get_tool_schemas(), sort_keys=True)

    # The system-prompt prefix and tool schema surface — the two things
    # frozen into the cached prefix — must be byte-identical after the write.
    assert prompt_after == prompt_before
    assert schemas_after == schemas_before
    # And the freshly written content must NOT have leaked into the prompt.
    assert "oat milk" not in prompt_after


def test_query_result_is_an_appended_message_not_prompt_mutation() -> None:
    provider = _provider_with_fake_store()
    provider.handle_tool_call("memory_write", {"content": "the wifi code is swordfish"})

    prompt_before = provider.system_prompt_block()
    raw = provider.handle_tool_call("memory_query", {"query": "wifi code"})
    payload = json.loads(raw)  # tool result is a JSON string (an appended msg)

    assert any("swordfish" in r["text"] for r in payload["results"])
    # Recall surfaced via the tool result, never by editing the system prompt.
    assert provider.system_prompt_block() == prompt_before
    assert "swordfish" not in provider.system_prompt_block()


def test_task_proposal_is_prefetched_once_without_prompt_mutation() -> None:
    provider = _provider_with_fake_store()
    discovery = _FakeDiscovery(provider._capture_task_proposal)
    provider._task_discovery = discovery  # type: ignore[assignment]
    prompt_before = provider.system_prompt_block()

    provider.on_turn_start(3, "send the weekly report")
    appended = provider.prefetch("send the weekly report")

    assert "Proposal for: send the weekly report" in appended
    assert "Approval result: not accepted" in appended
    assert provider.prefetch("send the weekly report") == ""
    assert provider.system_prompt_block() == prompt_before


def test_c4_task_session_activity_is_excluded_from_discovery() -> None:
    provider = _provider_with_fake_store()
    discovery = _FakeDiscovery(provider._capture_task_proposal)
    provider._task_discovery = discovery  # type: ignore[assignment]
    provider._task_session = True

    provider.on_turn_start(1, "work generated by the tracked task")

    assert discovery.origins == ["discovered_task"]
    assert provider.prefetch("work generated by the tracked task") == ""


def test_tools_are_service_gated_and_named() -> None:
    provider = SupabasePgvectorMemoryProvider()
    names = {schema["name"] for schema in provider.get_tool_schemas()}
    assert names == {"memory_query", "memory_write"}


def test_tool_call_when_store_unavailable_returns_error_json() -> None:
    provider = SupabasePgvectorMemoryProvider()  # no store initialised
    out = json.loads(provider.handle_tool_call("memory_query", {"query": "x"}))
    assert "error" in out


def test_memory_manager_prompt_and_tools_stable_across_write() -> None:
    """Through the real MemoryManager: prompt + tool surface stay frozen."""
    from agent.memory_manager import MemoryManager

    provider = _provider_with_fake_store()
    manager = MemoryManager()
    manager.add_provider(provider)

    prompt_before = manager.build_system_prompt()
    tools_before = json.dumps(manager.get_all_tool_schemas(), sort_keys=True)

    routed = manager.get_provider("supabase_pgvector")
    assert routed is provider
    provider.handle_tool_call("memory_write", {"content": "mid-turn fact xyz"})

    assert manager.build_system_prompt() == prompt_before
    assert json.dumps(manager.get_all_tool_schemas(), sort_keys=True) == tools_before
    assert "mid-turn fact xyz" not in manager.build_system_prompt()


def test_principal_resolution_fallbacks() -> None:
    provider = SupabasePgvectorMemoryProvider()
    # Explicit principal wins.
    explicit = provider._resolve_principal(
        {"principal_user_id": "u7", "principal_role": "admin"}
    )
    assert explicit.user_id == "u7" and explicit.role == "admin"
    # Gateway user id → member (own private tier).
    gw = provider._resolve_principal({"user_id": "tg-123"})
    assert gw.user_id == "tg-123" and gw.role == "member"
    # No identity → single owner (personal deployment default).
    local = provider._resolve_principal({})
    assert local.role == "owner"
