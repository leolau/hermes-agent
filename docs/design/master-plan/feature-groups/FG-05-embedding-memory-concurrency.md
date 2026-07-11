# FG-05 — Embedding memory with concurrency

**Wave:** 0 (foundation) · **Owner agent:** _unassigned_ · **Status:** Not started

## Summary
Add **semantic (embedding) memory** with **safe concurrent access** across many
`(user, task)` cores, implementing the **hybrid** consistency model (D2):
frozen curated snapshot in the prompt + a **live queryable store** (Postgres +
**pgvector**) read/written mid-turn via a **tool call** (never prompt injection).
Memory is **visibility-scoped** (D1/C2): shared vs per-user private; owner sees all.

## Decisions applied
- D2 (hybrid memory), D1/C2 (per-user private vs shared vs owner), D4 (pgvector in Supabase).

## Reuse map
- `tools/memory_tool.py` — the existing `MEMORY.md`/`USER.md` **frozen snapshot** (flock write-lock, threat-scan, atomic replace). **Keep as the "curated durable facts" side of hybrid.** Do NOT break its cache-safe snapshot semantics.
- `plugins/memory/*` (honcho, mem0, supermemory, hindsight, …) — existing memory-provider **ABC**; add a **Supabase/pgvector provider** as a first-class provider rather than a bespoke path.
- `hermes_state.py` WAL + FTS5 — concurrency reference; the live store uses Postgres MVCC instead (removes SQLite single-writer bottleneck).

## Design / approach
1. **Two tiers (hybrid):**
   - *Curated tier* = `MEMORY.md`/`USER.md` frozen snapshot at session start (unchanged; cache-safe).
   - *Live tier* = a `memory_query`/`memory_write` **tool** backed by Postgres+pgvector. Reads happen mid-turn and their **results are appended messages** — never spliced into the system prompt (cache-safe).
2. **Embeddings:** a `pgvector` column; embedding via a configured model
   (self-hosted default; provider pluggable). Similarity search + metadata
   filters (owner/visibility/topic/recency).
3. **Concurrency:** Postgres MVCC → concurrent readers + writers; each core
   writes its own rows; no cross-core lock contention. The curated snapshot
   remains eventually-consistent (only refreshes at next session start) — this
   is intended and documented.
4. **Scoping (C2):** every memory row carries `owner_user_id` + `visibility`;
   `memory_query` applies `scope_filter(principal)`; owner bypasses. RLS is the
   backstop.
5. **Promotion of a fact to curated `MEMORY.md`** stays owner/agent-curated
   (background_review pattern), so the prompt snapshot doesn't bloat.

## Data model (Supabase `app_*`)
- `memories(id, owner_user_id, visibility, kind, text, embedding vector, source_session, topic, created_at, last_used, uses)` + ivfflat/hnsw index on `embedding`; RLS by visibility.

## Dev/Prod + Supabase
Live store honours mode via C3 (`app_dev`/`app_prod`). Curated files are
profile-scoped as today.

## Testing requirements
- Unit: embedding write/query roundtrip; scope_filter correctness.
- **Concurrency test:** N concurrent writers + readers, no lost writes, no cross-`(user,task)` bleed.
- **Negative access test:** private memory of user B invisible to member A; owner sees it.
- **Cache-safety test:** a mid-turn `memory_query` does NOT mutate the system prompt (assert prompt prefix byte-stable across a turn that queries memory).
- Baseline green.

## System testing (existing ECS)
**Required step after this FG's development completes** (part of its Definition of Done), on top of the per-PR unit/E2E + baseline gate: deploy this FG to the existing ai-prentice ECS (`i-j6camnt3ocwlmzajthil`, 2/4, cn-hongkong) — the dedicated **system-test host** — and exercise it end-to-end on the real stack against a **staging** Supabase schema (`app_staging`) + staging SQLite core (**never prod**). See README §7.1. Acceptance checklist:
- Under **real concurrent `(user,task)` sessions** on the box, exercise pgvector semantic recall + concurrent writes; no lost writes, no cross-session bleed.
- Confirm per-user private vs shared memory scoping via **real RLS**; owner sees all.
- Confirm the frozen curated snapshot stays **cache-safe** (a mid-turn `memory_query` does not mutate the live system prompt).
- **Gate:** this FG is not complete/promotable until this ECS checklist passes (on top of the per-PR gate).

## Dependencies
- **Blocked by:** FG-13 (C3), FG-01 (C2 scoping).
- **Blocks:** FG-03 (shared coordination state), FG-04 (goal context), FG-06 (task context), FG-09.
- Consumes **C2, C3**; the D2 hybrid decision is finalised here (resolves the open item from `AGENT-HANDOFF.md §3`).

## Definition of Done
Tests green (incl. concurrency + negative access + cache-safety) + baseline green + `ruff`/`ty` clean; pgvector provider registered via the memory-provider ABC; curated snapshot semantics untouched; **ECS system test green**.

## Progress checklist
- [ ] Supabase/pgvector memory provider (via existing ABC)
- [ ] `memory_query`/`memory_write` tool (appended-result, cache-safe)
- [ ] visibility scoping + RLS
- [ ] concurrency + negative-access + cache-safety tests
- [ ] curated-tier snapshot untouched (regression check)
- [ ] System test on existing ECS passed (see *System testing* section)

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |
| 2026-07-11 | 2 | devin:8cec0d47 | Added System testing (existing ECS) section as a per-FG DoD step | Leo: existing ECS = system-test host, run after each FG's development |

## Cloud-agent prompt
> **[Wave 0 — start after FG-13 C3 + FG-01 C2 merge]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-05`). Implement **hybrid embedding memory with concurrency** (finalising the open memory-consistency decision in `docs/design/AGENT-HANDOFF.md §3`): keep `tools/memory_tool.py`'s frozen `MEMORY.md`/`USER.md` snapshot as the cache-safe curated tier (DO NOT break its snapshot semantics); add a **live tier** = a Supabase **Postgres + pgvector** memory provider registered through the existing `plugins/memory/*` ABC, exposed via `memory_query`/`memory_write` tools whose results are **appended messages** (never injected into the system prompt — prove cache-safety with a test). Scope every memory row with `owner_user_id` + `visibility` (contract C2) enforced by RLS; owner sees all. Use Postgres MVCC for concurrent `(user,task)` cores. Follow `AGENTS.md` (footprint ladder, cache-sacred, no core-tool growth). Add unit + **concurrency** + **negative-access** + **cache-safety** tests against temp `HERMES_HOME` + throwaway Postgres schema; keep `tests/plan_baseline/` green; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (existing ECS)* checklist (in this doc) passes** — coordinate that deploy/run with Leo.
