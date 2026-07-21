# FG-08 — Copy OSS capability + MCP (remote & in-house)

**Wave:** 2 · **Owner agent:** devin:ab45de26 · **Status:** In review (ECS system-test pending owner coordination)

## Summary
Based on a goal, the agent can acquire a capability from **open-source**, in one
of two modes (D3):
- **Remote system:** study an OSS project, **clone + host it on a different
  machine** with minimal/ideally no changes, and build an **MCP** to interface
  with it (≈ authoritative design **§4.3**, hosted off-box).
- **In-house system:** **build a new** tool on the ai-prentice-4-all box (default
  **Next.js + own Node process**, web UI + MCP) — via FG-07's scaffolder.

Both are **fully approval-gated**, provenance-tracked, and dev→prod promoted.

## Decisions applied
- D3 (remote vs in-house), D6/C6 (multi-approval gating), D5 (dev→prod), AGENTS.md (no third-party product trees in-core — remote OSS lives outside the repo tree; reach it via MCP).

## Reuse map
- `docs/design/architecture-design-number-one.md §4.3` — the authoritative 6-stage OSS pipeline (propose→vet→adapt→run→expose-MCP→retire) + hard rails (license allowlist, non-root, localhost/off-box only, pinned commit, ≥2 approvals). **Follow it for the remote path.**
- `tools/environments/*` (docker/ssh/modal/daytona/singularity) — sandboxed clone/run backends; remote host = an ssh/remote environment.
- FG-07 scaffolder — the in-house path.
- FG-11 MCP client + catalog — expose either as MCP.
- GitHub search (existing web/search tools) — repo discovery.

## Design / approach
1. **Discovery:** agent queries GitHub public repos for a stated goal, presents
   candidates (URL, license, activity, fit) → **user feedback/approval** (C6).
2. **Remote path (§4.3):** vet (license allowlist + supply-chain + secret scan +
   sandbox smoke) → clone to an **external host** with minimal changes → run
   non-root, network-restricted → wrap in a `fastmcp` server → register (FG-11).
   Provenance-tagged, commit-pinned, retire pass. **Kept OUT of the core repo
   tree** (it's someone else's product; per AGENTS.md we reach it via MCP, we
   don't vendor it under `plugins/`).
3. **In-house path:** when a clean build is preferred, use FG-07's Next.js
   scaffolder to (re)build the capability in-house with web UI + MCP.
4. **Choice heuristic:** default **remote/adapt-and-wrap** (cheaper, lower risk);
   **in-house rebuild** only on explicit request or when no suitable OSS exists.
5. Every stage emits C5 change events; two human approvals minimum (evaluate,
   apply).

## Data model
- Reuses FG-07 `tools` registry (`kind ∈ {remote, in_house}`) + FG-11 `mcp_endpoints`; a `provenance(tool_id, repo_url, license, commit, host, vetted_at)` record for remote systems.

## Dev/Prod + Supabase
Acquired systems start in **dev**; promoted to prod after validation (FG-13).
Registry/provenance in `app_*`.

## Testing requirements
- Unit: license-allowlist gate; approval-gate enforcement; provenance recorded.
- E2E (remote, mocked host): discover → approve → vet (reject disallowed license) → adapt → run in sandbox → MCP reachable → register.
- E2E (in-house): delegate to FG-07 scaffolder path.
- Negative: mint/irreversible not involved; unapproved acquisition cannot run.
- Baseline green.

## System testing (system-test box)
**Required step after this FG's development completes** (part of its Definition of Done), on top of the per-PR unit/E2E + baseline gate: deploy this FG to the new ai-prentice-4-all ECS (`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, 4/16, cn-hongkong-b, EIP `47.83.199.25`) — the dedicated **system-test host** — and exercise it end-to-end on the real stack against a **staging** Supabase schema (`app_staging`) + staging SQLite core (**never prod**). See README §7.1. Acceptance checklist:
- Remote path: clone + host a small **approved** OSS project on a **separate** host and reach it via MCP from the ECS; confirm the §4.3 hard rails (license allowlist, ≥2 approvals, non-root/network-restricted, commit-pin) are enforced live.
- In-house path: build a small tool via the FG-07 scaffolder on the box; confirm provenance recorded and dev→prod promotion works.
- **Gate:** this FG is not complete/promotable until this ECS checklist passes (on top of the per-PR gate).

## Dependencies
- **Blocked by:** FG-07 (scaffolder + registry), FG-11 (MCP), FG-12 (C5/C6).
- **Blocks:** FG-09 (tools as goal resources).

## Definition of Done
Tests green + baseline green + `ruff`/`ty` clean; remote path follows §4.3 hard rails incl. ≥2 approvals + license allowlist; no third-party OSS vendored into the core tree; in-house path reuses FG-07; **ECS system test green**.

## Progress checklist
- [x] GitHub discovery + candidate presentation (approval) — `oss discover`, fit ranking, license flag
- [x] Remote path: vet → clone off-box → run sandboxed → fastmcp wrap → register (§4.3 rails)
- [x] In-house path via FG-07 scaffolder (delegates to `scaffold_in_house_tool`)
- [x] Provenance + commit pin + retire pass (`provenance` registry, commit-pin rail, `oss retire`)
- [x] tests (unit + remote E2E mocked + in-house E2E + negative) green
- [ ] System test on the system-test ECS passed (see *System testing* section) — **pending owner coordination** (no access to the ECS box from this agent)

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |
| 2026-07-11 | 2 | devin:8cec0d47 | Added System testing (system-test box) section as a per-FG DoD step | Leo: new 4/16 ECS = system-test host (+ prod for now), run after each FG's development |
| 2026-07-11 | 3 | devin:ab45de26 | Implemented FG-08: `hermes_cli/oss_acquisition.py` (§4.3 6-stage remote pipeline + in-house delegation + license allowlist + host-runner abstraction + `fastmcp` wrapper generator), `hermes_cli/oss_provenance.py` (C2/C3 provenance registry + RLS), `hermes_cli/oss_host.py` (SSH host runner), `hermes_cli/oss_cmd.py` (`hermes oss` CLI), `acquire-oss-capability` skill; unit + real-path Postgres E2E (remote mocked-host + in-house + negative-access + mode-isolation) tests. Reuses FG-07 registry (`kind=remote/in_house`) + FG-11 endpoints + C1/C2/C3/C5/C6; no third-party OSS vendored in-core; no new `HERMES_*` config vars. | Execute the Cloud-agent prompt (final wave) |
| 2026-07-11 | 4 | devin:ab45de26 | Left the ECS system-test checklist item unchecked | This agent has no access to the system-test ECS box; that deploy/run is coordinated separately by Leo/the orchestrator |

## Cloud-agent prompt
> **[Wave 2 — start after FG-07 + FG-11 + FG-12 merge]** Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read `docs/design/master-plan/README.md`, this doc (`FG-08`), and `docs/design/architecture-design-number-one.md §4.3` (authoritative). Implement OSS acquisition in two modes (D3): (1) **Remote system** — follow §4.3's 6-stage pipeline (propose→vet→adapt→run→expose-MCP→retire) with ALL hard rails (license allowlist, supply-chain+secret scan, non-root, network-restricted, commit-pinned, **≥2 human approvals** via contract C6), cloning/hosting the project **on a different machine** (use `tools/environments/*` ssh/remote backends) and reaching it via a `fastmcp` MCP registered through FG-11 — do NOT vendor the third-party project under the core repo tree (per `AGENTS.md`); (2) **In-house system** — reuse FG-07's Next.js scaffolder (web UI + MCP). Default to remote/adapt-and-wrap; in-house rebuild only on explicit request or when no suitable OSS exists. Record provenance; start in dev, promote via FG-13; emit C5 change events. Add unit + remote E2E (mocked host) + in-house E2E + negative (license-reject, unapproved-cannot-run) tests; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist (in this doc) passes** — coordinate that deploy/run with Leo.
