# FG-08 — Copy OSS capability + MCP (remote & in-house)

**Wave:** 2 · **Owner agent:** _unassigned_ · **Status:** Not started

## Summary
Based on a goal, the agent can acquire a capability from **open-source**, in one
of two modes (D3):
- **Remote system:** study an OSS project, **clone + host it on a different
  machine** with minimal/ideally no changes, and build an **MCP** to interface
  with it (≈ authoritative design **§4.3**, hosted off-box).
- **In-house system:** **build a new** tool on the ai-prentice box (default
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

## Dependencies
- **Blocked by:** FG-07 (scaffolder + registry), FG-11 (MCP), FG-12 (C5/C6).
- **Blocks:** FG-09 (tools as goal resources).

## Definition of Done
Tests green + baseline green + `ruff`/`ty` clean; remote path follows §4.3 hard rails incl. ≥2 approvals + license allowlist; no third-party OSS vendored into the core tree; in-house path reuses FG-07.

## Progress checklist
- [ ] GitHub discovery + candidate presentation (approval)
- [ ] Remote path: vet → clone off-box → run sandboxed → fastmcp wrap → register (§4.3 rails)
- [ ] In-house path via FG-07 scaffolder
- [ ] Provenance + commit pin + retire pass
- [ ] tests (unit + remote E2E mocked + in-house E2E + negative) green

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |

## Cloud-agent prompt
> **[Wave 2 — start after FG-07 + FG-11 + FG-12 merge]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md`, this doc (`FG-08`), and `docs/design/architecture-design-number-one.md §4.3` (authoritative). Implement OSS acquisition in two modes (D3): (1) **Remote system** — follow §4.3's 6-stage pipeline (propose→vet→adapt→run→expose-MCP→retire) with ALL hard rails (license allowlist, supply-chain+secret scan, non-root, network-restricted, commit-pinned, **≥2 human approvals** via contract C6), cloning/hosting the project **on a different machine** (use `tools/environments/*` ssh/remote backends) and reaching it via a `fastmcp` MCP registered through FG-11 — do NOT vendor the third-party project under the core repo tree (per `AGENTS.md`); (2) **In-house system** — reuse FG-07's Next.js scaffolder (web UI + MCP). Default to remote/adapt-and-wrap; in-house rebuild only on explicit request or when no suitable OSS exists. Record provenance; start in dev, promote via FG-13; emit C5 change events. Add unit + remote E2E (mocked host) + in-house E2E + negative (license-reject, unapproved-cannot-run) tests; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc.
