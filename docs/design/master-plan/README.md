# Hermes / ai-prentice — Master Implementation Plan

> **Status:** PLAN (no feature code yet). This document + the per-feature-group
> docs under [`feature-groups/`](./feature-groups/) are the single source of
> truth for the 13-feature-group build-out. Baseline regression tests live in
> `tests/plan_baseline/`.
>
> **Scope:** turn the single-owner personal Hermes deployment (ai-prentice) into
> a **multi-user, multi-channel, self-improving agent platform** for an
> organisation (school / company), while respecting the existing architecture's
> hard constraints (prompt-cache safety, one-brain, footprint ladder).

---

## 0. How to read / maintain this plan

- **This README** = cross-cutting decisions, principles, dependency waves,
  parallelisation, testing strategy, governance, and the **edition/audit log**.
- **`feature-groups/FG-XX-*.md`** = one doc per feature group. Each is
  self-contained: reuse map, design, data model, dev/prod + Supabase notes,
  testing requirements, dependencies, wave, definition-of-done, a live
  **progress checklist**, a **per-FG audit log**, and a **ready-to-paste Devin
  cloud-agent prompt**.
- **`agent-prompts.md`** = all 13 cloud-agent prompts in one place.

### Governance / edition tracking (IMPORTANT for parallel agents)
- The **master plan changelog** (§9) is *append-only*. Any change to
  cross-cutting scope, decisions, waves, or contracts adds a row — never
  rewrites history.
- Each feature-group agent edits **only its own `FG-XX-*.md`** (its progress
  checklist + its own audit log). It must **not** edit other FG docs or the
  master changelog except to append one row to §9 when it changes a shared
  contract.
- Every audit entry uses this format:
  `YYYY-MM-DD | edition | author (user / devin:<session>) | FG | change | rationale`.
- Editions are integer, monotonically increasing per document.

---

## 1. Locked decisions (from Leo, this planning session)

| # | Decision | Consequence |
|---|----------|-------------|
| D1 | **Multi-user, NOT multi-tenant.** One shared brain; three-tier visibility: **shared** org knowledge, **per-user private** memory/skills, **owner** sees everything. | Every memory/skill/goal/task/tool/asset row carries `owner` + `visibility` (`shared` \| `private:<user_id>`); reads filtered by requesting principal; owner bypasses. Ownership is transferable (approval-gated). |
| D2 | **Memory consistency = HYBRID.** | Curated durable facts → frozen `MEMORY.md`/`USER.md` snapshot in the prompt (cache-safe). Volatile/coordination state + embeddings → live **queryable store** read/written mid-turn **via a tool call** (never prompt injection). |
| D3 | **OSS integration has two modes.** | **Remote system**: study OSS, clone + host on a *different* machine with minimal/no changes, expose via MCP (≈ design-doc §4.3). **In-house system**: build a *new* tool on the ai-prentice box, default **Next.js, one Node process per tool**, with **two interfaces: web UI (human) + MCP (agent)**. |
| D4 | **Datastore = bounded hybrid.** | **SQLite** stays for the Hermes *agent core* (SessionDB, kanban, projects, checkpoint, frozen memory files). **Self-hosted Supabase (Postgres + pgvector + GoTrue + Realtime + Storage)** is the datastore for the **new multi-user application layer** (identity/access, embeddings, goals/tasks/tools/change-log, in-house-tool data). |
| D5 | **Dev vs Prod.** | User-developed tools/skills/config start in **dev** (dev DB), promoted to **prod** on confirmation. Provide a **dev Supabase database/schema**. **Incoming channels are PROD-ONLY** (no dev channels). |
| D6 | **Blockchain (2.0) is opt-in + gated.** | DID:ION per user (hosted resolver first); ERC-721 mint per digital asset is the **explicit exception to undo** — **irreversible, must be user-triggered AND user-approved** (agent may never mint autonomously). Ship as plugin + MCP, testnet-first. |
| D7 | **`session_key` gains dimensions.** | `session_key = f(channel identity, account_id, internal user, task)` to maximise prompt-cache locality and isolate `(user, task)` cores. Extends `SessionSource` (adds `account_id`) + the key builder. |
| D8 | **Infra.** | Migrate the ai-prentice ECS to **`ecs.e-c1m4.xlarge` (4 vCPU / 16 GB)** first, **dedicated ESSD data disk** for Supabase, **EIP** for a stable IP; **in-place resize to `ecs.e-c1m4.2xlarge` (8/32)** when needed (same-family resize, ~5 min stop/start, no data migration). |
| D9 | **Delivery = parallel Devin cloud agents**, one per FG, coordinated in **dependency waves**. Shared contracts merge first (Wave 0). | See §5, §6. |

Cost context (see chat): current 2/4 box ≈ **$36/mo**; target 4/16 ≈ **$137/mo + ~$15 disk**; 8/32 ≈ **$266–317/mo**.

---

## 2. Architectural principles (the review bar — from `AGENTS.md`)

Every FG must obey these or it will not merge:

1. **Prompt caching is sacred.** The system prompt must stay **byte-stable
   within a conversation**. Never inject fresh memory/goals/tools into the
   system prompt mid-conversation, never hot-swap a live conversation's
   toolset, never reorder/edit past messages. Surface new knowledge via
   **appended tool-call results** or **appended continuation messages**
   (this is exactly what `hermes_cli/goals.py` does today — the reference
   pattern). The one sanctioned exception is context compression.
2. **One brain, one profile.** All channels/users share `HERMES_HOME=/opt/data`
   — one skill store, one memory store, one session DB. Multi-user is an
   **access-control layer over the shared brain**, not separate profiles.
3. **Footprint ladder.** New capability arrives at the **highest (least core)**
   rung that works: extend existing code → CLI+skill → service-gated tool
   (`check_fn`) → plugin → MCP server → (last resort) new core tool. Almost
   nothing here should become a new *core model tool*.
4. **Extend, don't duplicate.** Reuse the primitives listed per FG. Do not add
   a 4th goal/task store, a 2nd approval framework, etc.
5. **Behavior/invariant tests, not change-detector tests.** Assert how data
   must relate; do not freeze current values (counts, model lists, config
   versions). Exercise real paths against a temp `HERMES_HOME`.
6. **`.env` = secrets only.** All behavioural config in `config.yaml` /
   Supabase, never new `HERMES_*` env vars for non-secrets.

---

## 3. The 13 feature groups (index)

| FG | Title | Wave | Primary reuse anchors |
|----|-------|------|-----------------------|
| [01](./feature-groups/FG-01-multi-user-access.md) | Multi-users with access rights; single transferable owner | **0** | `gateway/authz_mixin.py`, `gateway/pairing.py`, `dashboard_auth/`, Supabase GoTrue + RLS |
| [02](./feature-groups/FG-02-blockchain-did-erc721.md) | Blockchain per user: DID:ION + ERC-721 assets | 2 | `optional-skills/blockchain/evm`, MCP rung, approval gates |
| [03](./feature-groups/FG-03-multi-channel-redesign.md) | Multi-channel redesign (one brain, all channels) | 1 | `gateway/`, `gateway/session.py`, `custom/*`, design docs #1/#2 |
| [04](./feature-groups/FG-04-goals-priority-measurability.md) | Goals with priority + measurability/progress | 1 | `hermes_cli/goals.py` (`GoalState`/`GoalContract`/judge) |
| [05](./feature-groups/FG-05-embedding-memory-concurrency.md) | Embedding memory with concurrency | **0** | `tools/memory_tool.py`, `plugins/memory/*`, Supabase pgvector |
| [06](./feature-groups/FG-06-task-discovery-progress.md) | Task discovery & progress tracking | 1 | `tools/todo_tool.py`, `tools/kanban_tools.py`, `projects_db.py` |
| [07](./feature-groups/FG-07-tools-creation-dashboard.md) | Tools creation & configuration + Dashboard | 2 | `hermes_cli/tools_config.py`, `hermes mcp`, `web/`, catalog |
| [08](./feature-groups/FG-08-oss-copy-mcp.md) | Copy OSS capability + MCP (remote & in-house) | 2 | design §4.3, terminal sandbox backends, `hermes mcp` |
| [09](./feature-groups/FG-09-goal-management.md) | Management of goals: memory + tasks + tools | 3 | FG-04/05/06/07 + `mcp_serve.py` |
| [10](./feature-groups/FG-10-human-comms.md) | Human comms: Telegram + web app | 2 | `gateway/` telegram, `clarify_gateway`, `approval`, `web/` |
| [11](./feature-groups/FG-11-agent-comms-mcp.md) | Agent comms: MCP | 1 | `mcp_serve.py`, `tools/mcp_tool.py`, catalog |
| [12](./feature-groups/FG-12-change-management.md) | Change management (data/config/code) + undo/redo/approve/backup | 1 | `tools/checkpoint_manager.py`, `approval`, `write_approval`, `backup.py` |
| [13](./feature-groups/FG-13-dev-prod-mode.md) | Dev vs Prod mode + dev SQLite/Supabase (channels prod-only) | **0** | `hermes_constants.py`, `hermes_state.py`, `config.yaml` |

---

## 4. Cross-cutting shared contracts (Wave 0 — must merge FIRST)

These are the seams every later FG consumes. They are small, additive, and
land before any parallel feature work so agents don't collide on the god-files
(`cli.py`, `run_agent.py`, `hermes_state.py`, `gateway/run.py`).

- **C1 — Principal/identity model** (FG-01). `Principal{user_id, role∈{owner,admin,member,viewer}, ...}`; a `resolve_principal(source)` seam in the gateway; `owner` transfer op. Backed by Supabase GoTrue.
- **C2 — Visibility/scoping helper** (FG-01 + FG-05). `visibility ∈ {shared, private:<user_id>}` + `can_read(principal, row)` / `scope_filter(principal)` used by memory, skills, goals, tasks, tools, assets.
- **C3 — Datastore router** (FG-13 + FG-04-DB). One accessor that returns the correct connection/schema for **(mode: dev|prod)** and **(store: sqlite-core | supabase-app)**. Everything DB-touching goes through it. Channels force `mode=prod`.
- **C4 — `SessionSource.account_id` + extended `session_key`** (FG-03 + D7). Additive field; `build_session_key` folds in `account_id` (+ user/task where applicable) while remaining **byte-identical for existing single-account callers** (regression-locked by `tests/plan_baseline/test_session_key_baseline.py`).
- **C5 — Change-event schema** (FG-12). Append-only `changes(id, ts, actor, mode, target_kind∈{data,config,code}, op, inverse_op|null, reversible:bool, approval_ref, backup_ref)`; every mutating capability emits one.
- **C6 — Approval/consent policy object** (FG-10 + FG-12 + FG-06/04). One policy surface (reuse `tools/approval.py` + `write_approval.py`) with quiet-hours/rate-limit/consent, shared by proactive messaging (4.1/6.1), change approvals (12), and action gating.

Wave-0 agents publish these as typed interfaces + docstrings + baseline tests
**before** Wave-1 agents start.

---

## 5. Dependency graph & waves (for parallel development)

```
WAVE 0 (foundations — merge before anything else; can run 3 agents in parallel,
         but they co-own C1–C6 so land them as small contract PRs first)
  ├─ FG-13  dev/prod mode + datastore router (C3)              ─┐
  ├─ FG-01  multi-user identity/access (C1, C2)                 ├─ contracts C1–C6
  └─ FG-05  embedding memory + concurrency (C2 uses, pgvector)  ─┘

WAVE 1 (core capabilities — parallel; each owns a distinct subsystem)
  ├─ FG-03  multi-channel redesign      (needs C3, C4; needs D2 memory model)
  ├─ FG-04  goals + priority + metrics  (needs C2, C3)
  ├─ FG-06  task discovery + progress   (needs C2, C3; feeds from FG-03 convo)
  ├─ FG-11  agent comms MCP             (needs C1 for auth)
  └─ FG-12  change management           (needs C3, C5; publishes C6 approval)

WAVE 2 (needs Wave 1 + C6 approval frozen — parallel)
  ├─ FG-07  tools creation + dashboard  (needs C3, C5, C6; web/)
  ├─ FG-08  OSS remote + in-house       (needs C6, FG-07 tool-registry, sandbox)
  ├─ FG-10  human comms webapp parity   (needs C1, C6, web/)
  └─ FG-02  blockchain DID + ERC-721    (needs C1, C6; plugin+MCP)

WAVE 3 (integration)
  └─ FG-09  goal management = memory+tasks+tools across sources/telegram/webapp/MCP
             (needs FG-04, 05, 06, 07, 10, 11)
```

**Ordering rules for agents:**
- An FG agent may start only when **all its "blocked-by" FGs have merged** (see
  each FG doc's *Dependencies* section).
- Within a wave, agents work in **separate modules/plugins**; edits to shared
  god-files must go through the Wave-0 contract seams, not ad-hoc.
- FG-03 must define/merge `account_id` (C4) early in Wave 1 because FG-06 and
  FG-09 build on the per-`(user,task)` session shape.

---

## 6. Parallel Devin cloud agents

Each FG doc ends with a **ready-to-paste prompt**; all are collected in
[`agent-prompts.md`](./agent-prompts.md). To launch:

- **Manual:** open a new Devin session, paste the FG prompt. Start only FGs
  whose dependencies are merged (respect the waves).
- **Automatic (offered after this plan PR merges):** the orchestrator session
  can spawn child sessions **wave-by-wave** — Wave 0 first, then fan out.
  Never launch all 13 at once; Waves 1–3 depend on Wave-0 contracts.

Every agent must: work on its own branch, edit only its FG doc, keep the
baseline suite green, add its own FG tests, run lint+typecheck, open a PR that
links back to its FG doc.

---

## 7. Testing strategy

Follows the repo's harness (`scripts/run_tests.sh`, per-file isolation,
hermetic `HERMES_HOME`, CI: `tests.yml` / `typecheck.yml` (ty) / `lint.yml`
(ruff `PLW1514`)) and doctrine (invariant/behavior tests, real E2E vs temp
`HERMES_HOME`, **no change-detector tests**).

- **Baseline regression suite (`tests/plan_baseline/`, delivered with this plan):**
  pins the invariants of the primitives every FG extends, so any FG that
  regresses a reuse anchor fails immediately. Current coverage:
  - `test_session_key_baseline.py` — `build_session_key` determinism + per-chat / per-user isolation + namespace shape (locks C4: FG-03 must keep these byte-stable for single-account callers).
  - `test_goal_state_baseline.py` — `GoalState`/`GoalContract` JSON round-trip, defaults, back-compat load of old rows (locks FG-04's base).
  - `test_todo_store_baseline.py` — todo status vocabulary + transition/merge semantics + prompt-injection of only pending/in-progress (locks FG-06's base).
- **Per-FG tests (each FG delivers):** unit + at least one **real-path E2E**
  against a temp `HERMES_HOME` (and a throwaway Postgres/Supabase schema where
  the FG touches the app DB). Multi-user FGs must include a **negative access
  test** (a `private:<other>` row is NOT visible to a different member; owner
  sees it).
- **Definition of Done (every FG):** new FG tests green **AND** full baseline
  suite still green **AND** `ruff`/`ty` clean, before the FG is marked complete
  in its doc. "Enough testing coverage to confirm the new feature group works
  without bugs and did not cause regression" (Leo) is enforced by this gate.

Run: `scripts/run_tests.sh tests/plan_baseline/` (fast) and the FG's own path.

### 7.1 System testing environment (existing ECS)

There are **three** distinct places tests run — keep them separate:

| Layer | Where it runs | When | Data |
|-------|---------------|------|------|
| Baseline + per-FG unit/E2E | each Devin agent's VM + **CI** (per PR) | continuously, during development | temp `HERMES_HOME` + **throwaway** Postgres schema |
| **System / integration testing** | **the existing ai-prentice ECS** (`i-j6camnt3ocwlmzajthil`, 2 vCPU/4 GB, cn-hongkong) as the dedicated **system-test host** | **after EACH feature group's development completes** (a required step in that FG's Definition of Done) | **staging** Supabase schema (`app_staging`) + a staging SQLite core on the box — **never prod** |
| Production | the new ECS (`ecs.e-c1m4.xlarge`, 4/16, per D8) | after an FG's system test passes and it is promoted | prod Supabase + prod `state.db` |

**Why the existing box:** per Leo, the current 2/4 ai-prentice ECS is the shared
**system-test host** — it is **not** a development box. Every feature group,
once its code is developed and its per-PR unit/E2E + baseline gate is green, is
deployed to this ECS and exercised **end-to-end on the real stack** before that
FG is considered done. Production is the *new* box.

**How the per-FG system test works (repeated for every FG):**
1. The FG's PR passes CI (baseline + its own unit/E2E, `ruff`/`ty`).
2. The FG is deployed to the existing ECS in **staging mode** (`mode=dev`/
   `staging` via contract C3; `app_staging` Supabase schema + staging SQLite
   core) on top of the already-merged FGs.
3. Run **this FG's "System testing (existing ECS)" acceptance checklist** (see
   its FG doc) against the real deployed stack — real GoTrue/RLS, real pgvector,
   real channel adapters bound to **test** accounts, real Telegram + web app,
   real MCP endpoints.
4. **Definition-of-Done gate:** the FG is not complete/promotable until its ECS
   system-test checklist is green (on top of the per-PR gate). Only then is it
   promoted to production on the new box.

**Ordering note:** because each FG's system test runs on the *cumulative*
deployed stack, an FG whose live behaviour depends on a not-yet-merged FG
verifies what it can in isolation and re-runs the cross-FG checks once its
dependency lands (see each FG's *Dependencies*). Cross-surface end-to-end
coverage is owned by **FG-09**.

**Resource caveat:** the full self-hosted Supabase bundle + Node tools on the
2/4 box is tight; run each FG's system test **sequentially** (not all channels/
tools hot at once), or temporarily in-place-resize the system-test box for the
pass (same-family resize, ~5 min, no data migration — D8).

---

## 8. Risks / open items carried into implementation

- **Supabase resource budget** on a 4/16 box with the full bundle + Node tools
  + concurrent cores — monitor; resize to 8/32 (D8) if RAM-bound.
- **Multi-user vs upstream Hermes divergence** — keep the access layer as an
  additive seam so upstream merges stay tractable.
- **Proactive messaging (4.1/6.1)** must ride C6 (quiet-hours/rate-limit/
  consent) or it becomes spam; also guard against self-generated task loops.
- **ERC-721 irreversibility** (D6) is explicitly outside undo (12.1).
- **`alibabacloud` MCP server currently fails to init** — infra used the
  `aliyun` CLI instead; flagged separately.

---

## 9. Master plan changelog (append-only)

| Date | Edition | Author | Scope | Change | Rationale |
|------|---------|--------|-------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 (for Leo) | all | Initial master plan + 13 FG docs + baseline tests | Kickoff of the 13-FG build-out; decisions D1–D9 locked in planning session |
| 2026-07-11 | 2 | devin:8cec0d47 (for Leo) | testing | Added §7.1 + a "System testing (existing ECS)" section to every FG doc, as a required Definition-of-Done step after each FG's development | Leo: use the existing ai-prentice ECS as the system-test host, exercised after every feature group's development (per-FG, not a single post-all-waves pass) |
