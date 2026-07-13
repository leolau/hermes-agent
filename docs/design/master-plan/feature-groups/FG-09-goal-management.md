# FG-09 — Management of goals: memory + tasks + tools

**Wave:** 3 (integration) · **Owner agent:** devin:201d283b · **Status:** Implemented (code + tests green) — merged to `develop`; ECS system test is the remaining owner-gated step

## Summary
The **integration layer**: unify **goals** (FG-04) with the **memory** (FG-05),
**tasks** (FG-06), and **tools** (FG-07/08) that serve them, manageable across
**all sources**: incoming **channels**, **Telegram**, the **web app**, and
**MCP**. A goal becomes the organising context that pulls in the memory it needs,
the tasks that advance it, and the tools that execute it.

## Decisions applied
- All prior decisions; this FG wires them together. D1/C2 scoping across every resource; cache-safety in how goal context is surfaced.

## Reuse map
- FG-04 goal registry + metrics; FG-05 memory; FG-06 tasks; FG-07/08 tools; FG-11 MCP; FG-10 human surfaces; FG-03 channels.
- `mcp_serve.py` (FG-11 surface) — MCP management of goals/tasks/memory/tools.

## Design / approach
1. **Goal-centric linking:** a goal references its `memory` (relevant scoped
   entries / topics), `tasks` (advancing it), and `tools` (able to execute).
   Links are scoped (C2); owner sees all.
2. **Uniform management across sources:** the same goal-management operations
   (list/create/prioritise/link/advance/close) available from **channels,
   Telegram, web app, and MCP** — one service layer, four front-ends. Reads are
   scope-filtered; writes gated by C6 where needed.
3. **Context assembly (cache-safe):** when a `(user, task)` core works a goal,
   it pulls relevant memory/tasks/tools via **tool calls whose results are
   appended** — never by mutating the system prompt.
4. **Cross-resource consistency:** a task completing / metric hitting target
   updates the goal; a tool retiring flags dependent goals.

## Data model (`app_*`)
- `goal_links(goal_id, resource_kind ∈ {memory, task, tool}, resource_ref, created_at)` — scoped join; all four front-ends read/write through one service.

## Dev/Prod + Supabase
Management operates per mode (C3). Channel-sourced management is prod-only.

## Testing requirements
- Unit: goal↔memory/task/tool linking + scope; cross-resource update propagation.
- Negative access: a member managing goals sees only permitted resources; owner sees all.
- E2E across surfaces: create+link a goal via web, advance a linked task via Telegram, query it via MCP, and confirm a channel-sourced update — all consistent, all scope-correct, all cache-safe.
- Baseline green.

## System testing (system-test box)
**Required step after this FG's development completes** (part of its Definition of Done), on top of the per-PR unit/E2E + baseline gate: deploy this FG to the new ai-prentice ECS (`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, 4/16, cn-hongkong-b, EIP `47.83.199.25`) — the dedicated **system-test host** — and exercise it end-to-end on the real stack against a **staging** Supabase schema (`app_staging`) + staging SQLite core (**never prod**). See README §7.1. As the final integration FG, this also owns the **cross-surface system-test pass** over the fully assembled stack. Acceptance checklist:
- End-to-end across **all four surfaces** (a channel, Telegram, the web app, MCP): create + link a goal, advance a linked task, query via MCP, and push a channel-sourced update — confirm all four stay consistent, scope-correct (C2), and cache-safe.
- Re-run the earlier FGs' ECS checklists on the integrated stack to confirm no cross-FG regression.
- **Gate:** this FG is not complete/promotable until this ECS checklist passes (on top of the per-PR gate).

## Dependencies
- **Blocked by:** FG-04, FG-05, FG-06, FG-07, FG-10, FG-11 (and FG-03 for channel source, FG-08 for tools, FG-12 for change events).
- **Blocks:** — (final integration).

## Definition of Done
Tests green (incl. cross-surface E2E + negative access) + baseline green + `ruff`/`ty` + web lint/typecheck clean; one service layer drives all four front-ends; goal↔memory/task/tool links consistent and scoped; cache-safe context assembly; **ECS system test green**.

## Progress checklist
- [x] Goal↔memory/task/tool linking (scoped)
- [x] Unified goal-management service (channels/Telegram/web/MCP front-ends)
- [x] Cache-safe context assembly (appended tool results)
- [x] Cross-resource consistency propagation
- [x] tests (unit + negative + cross-surface E2E) green
- [ ] System test on the system-test ECS passed (see *System testing* section)

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |
| 2026-07-11 | 2 | devin:8cec0d47 | Added System testing (system-test box) section as a per-FG DoD step | Leo: new 4/16 ECS = system-test host (+ prod for now), run after each FG's development |
| 2026-07-11 | 3 | devin:201d283b | Implemented scoped goal links, one shared four-surface service, cache-safe context, cross-resource propagation, and unit/real-path E2E coverage | Completed the local FG-09 integration gate; ECS/system testing remains pending owner/orchestrator coordination |
| 2026-07-04 | 4 | devin:3c64bcf2 | Corrected stale header (`Not started` → `Implemented … merged to develop; ECS system test remaining`) | Branch is contained in `develop` and checklist is all `[x]` except the owner-gated ECS test; header was misleading |

## Cloud-agent prompt
> **[Wave 3 — start after FG-04, 05, 06, 07, 10, 11 merge]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-09`). Build the **integration layer** that unifies **goals + memory + tasks + tools** and exposes goal management uniformly across **incoming channels, Telegram, the web app, and MCP** via ONE service layer with four front-ends. Add scoped `goal_links(goal_id, resource_kind∈{memory,task,tool}, resource_ref)` (contract C2; owner sees all). Assemble goal context **cache-safely** — pull relevant memory/tasks/tools via tool calls whose results are **appended** (never mutate the system prompt). Propagate cross-resource updates (task done / metric hit → goal; tool retired → dependent goals flagged); channel-sourced management is prod-only (contract C3). Follow `AGENTS.md` (cache-sacred, footprint ladder, extend not duplicate). Add unit + negative-access + **cross-surface E2E** tests; run `scripts/run_tests.sh`, `ruff`, `ty` + web lint/typecheck. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist (in this doc) passes** — coordinate that deploy/run with Leo.
