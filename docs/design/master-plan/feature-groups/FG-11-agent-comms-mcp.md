# FG-11 — Agent communications: MCP

**Wave:** 1 · **Owner agent:** _unassigned_ · **Status:** Not started

## Summary
Standardise **agent↔agent / agent↔tool** communication on **MCP**, in both
directions: Hermes **as an MCP server** (other agents/tools drive Hermes) and
Hermes **as an MCP client** (Hermes drives external/in-house MCP servers).
Access is principal-aware (C1) so MCP callers act under a scoped identity.

## Decisions applied
- D3 (in-house tools expose an MCP interface; remote systems reached via MCP), D1/C1 (MCP callers carry a principal/scope), footprint ladder (MCP is the preferred rung for non-core capability).

## Reuse map
- `mcp_serve.py` — existing stdio MCP server exposing Hermes (conversations/messages/events/permissions + `channels_list`). **Extend, don't fork.**
- `tools/mcp_tool.py` + `hermes mcp add/install/serve` + MCP catalog (`optional-mcps/`) — client side + registration.
- `gateway/authz_mixin.py` / C1 — map an MCP session to a `Principal`.

## Design / approach
1. **Server side:** extend `mcp_serve.py` surface so agent peers can query
   goals/tasks/memory/tools **through scoped, principal-aware tools** (reads
   filtered by C2; writes gated by C6 where mutating). Keep the tool surface
   stable + documented (it's an interface contract for FG-09).
2. **Client side:** a uniform way to register + call in-house tool MCPs (FG-07)
   and remote-system MCPs (FG-08) via the catalog; never splice a new MCP tool
   into a *live* conversation's toolset (cache-safety) — new tools are available
   to **future** sessions.
3. **Identity:** each MCP connection resolves to a `Principal`; unauthenticated
   peers get `viewer` at most.

## Data model
No new store; leans on FG-01 principals + FG-07/08 tool registry. An
`mcp_endpoints(id, name, kind ∈ {in_house, remote}, transport, scope, mode)`
registry row per connected server (shared with FG-07/08).

## Dev/Prod + Supabase
MCP endpoints carry `mode`; dev endpoints only reachable in dev sessions (C3).

## Testing requirements
- Unit: MCP server tool surface stable; principal resolution → scope applied.
- Negative: a `viewer`-scoped MCP caller cannot read `private:<other>` or perform gated writes.
- E2E: register an in-house MCP endpoint, call a tool through the client, assert scope + no live-conversation toolset mutation.
- Baseline green.

## System testing (existing ECS)
**Required step after this FG's development completes** (part of its Definition of Done), on top of the per-PR unit/E2E + baseline gate: deploy this FG to the existing ai-prentice ECS (`i-j6camnt3ocwlmzajthil`, 2/4, cn-hongkong) — the dedicated **system-test host** — and exercise it end-to-end on the real stack against a **staging** Supabase schema (`app_staging`) + staging SQLite core (**never prod**). See README §7.1. Acceptance checklist:
- A **peer agent** connects to the deployed Hermes over real MCP; confirm principal-aware **scoped reads** (C2) + consent-gated writes (C6); unauthenticated ⇒ `viewer`.
- Register + call one in-house and one remote MCP endpoint from the box; confirm **no live-conversation toolset splicing** (new tools serve future sessions only).
- **Gate:** this FG is not complete/promotable until this ECS checklist passes (on top of the per-PR gate).

## Dependencies
- **Blocked by:** FG-01 (C1/C2).
- **Blocks:** FG-07, FG-08, FG-09 (they consume the MCP surface + registry).

## Definition of Done
Tests green + baseline green + `ruff`/`ty` clean; `mcp_serve.py` extended (not forked); MCP surface documented as a contract; cache-safety preserved (no live toolset splicing); **ECS system test green**.

## Progress checklist
- [ ] Extend `mcp_serve.py` with scoped, principal-aware tools
- [ ] Uniform in-house/remote MCP registration via catalog
- [ ] Principal resolution for MCP connections
- [ ] endpoint registry (`mode`-aware)
- [ ] tests (unit + negative + E2E) green
- [ ] System test on existing ECS passed (see *System testing* section)

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |
| 2026-07-11 | 2 | devin:8cec0d47 | Added System testing (existing ECS) section as a per-FG DoD step | Leo: existing ECS = system-test host, run after each FG's development |

## Cloud-agent prompt
> **[Wave 1 — start after FG-01 merges]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-11`). Standardise **agent comms on MCP** both directions. Server side: EXTEND `mcp_serve.py` (do not fork) with **principal-aware, scoped** tools so peer agents can query goals/tasks/memory/tools with reads filtered by contract C2 and mutating writes gated by contract C6; keep the surface stable + documented (it's the interface FG-09 consumes). Client side: a uniform registration path (reuse `tools/mcp_tool.py` + `hermes mcp` + the `optional-mcps/` catalog) for in-house (FG-07) and remote (FG-08) MCP servers, with a `mode`-aware `mcp_endpoints` registry. Map every MCP connection to a `Principal` (contract C1; unauthenticated ⇒ `viewer`). NEVER splice a new MCP tool into a live conversation's toolset — new tools serve future sessions only (cache-safety). Follow `AGENTS.md` (MCP is the preferred non-core rung; no core-tool growth). Add unit + negative-scope + E2E tests; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (existing ECS)* checklist (in this doc) passes** — coordinate that deploy/run with Leo.
