# FG-02 — Blockchain per user: DID:ION + ERC-721 assets

**Wave:** 2 · **Owner agent:** _unassigned_ · **Status:** Not started

## Summary
Give each user a **decentralised identity (DID:ION)** and let users mint an
**ERC-721** token per **digital asset created by the user/agent**. Minting is
the **explicit exception to undo (D6): irreversible, user-triggered AND
user-approved** — the agent may never mint autonomously.

## Decisions applied
- D6 (opt-in, testnet-first, mint irreversible + user-triggered + user-approved), D1/C1 (DID bound to principal), C5 (mint recorded as `reversible=false`), footprint ladder + AGENTS.md (blockchain is a vendor capability → optional-skill + MCP, NOT a new in-tree plugin/core tool).

## Reuse map
- `optional-skills/blockchain/evm` (existing EVM optional-skill) — extend for ERC-721; keep heavy/niche capability off the default path.
- FG-01 principals — DID binds to `Principal.user_id`.
- FG-11 MCP + `hermes mcp` — expose chain ops as an MCP server (preferred rung).
- FG-12 change log — mint recorded, marked irreversible.
- `tools/approval.py` / C6 — mandatory user approval for mint.

## Design / approach
1. **DID:ION per user:** create/resolve a DID bound to the principal; store the
   DID document reference. Use a **hosted ION resolver** first (no self-run ION
   node); keys custodied per policy (start custodial + opt-in, document upgrade
   path to non-custodial).
2. **ERC-721 per asset:** when a user creates a digital asset, they MAY mint a
   token representing it. Mint flow: user **initiates** → agent prepares tx +
   shows cost/chain/testnet → user **approves** (C6) → submit → record in C5 as
   `reversible=false`. Agent-autonomous mint is blocked at the policy layer.
3. **Testnet-first:** default to a testnet/L2; mainnet only on explicit config +
   per-mint approval. Gas/cost surfaced before approval.
4. **Surface:** chain ops via an **MCP server** (registered through FG-11) +
   an optional-skill for guidance — not a new core tool, not vendored under
   `plugins/`.

## Data model (`app_*`)
- `user_dids(user_id FK, did, doc_ref, method='ion', created_at)`.
- `asset_tokens(id, asset_ref, owner_user_id, chain, contract, token_id, tx_hash, minted_at)` — mint recorded + linked to a C5 event with `reversible=false`.

## Dev/Prod + Supabase
Testnet = the safe default (akin to dev); mainnet mint = prod + explicit
approval. Registry in `app_*`.

## Testing requirements
- Unit: DID create/resolve (mocked resolver); mint flow requires user-trigger + approval; agent-autonomous mint rejected.
- **Irreversibility test:** mint emits a C5 event with `reversible=false`; undo refuses it (cross-checks FG-12).
- E2E (testnet/mocked chain): asset → user-initiated mint → approval → token recorded; no-approval path blocked.
- Baseline green.

## Dependencies
- **Blocked by:** FG-01 (C1), FG-11 (MCP), FG-12 (C5/C6 + irreversible marking).
- **Blocks:** —

## Definition of Done
Tests green (incl. irreversibility + autonomous-mint-blocked) + baseline green + `ruff`/`ty` clean; DID bound to principal; mint strictly user-triggered + user-approved; capability shipped as optional-skill + MCP (not core/in-tree plugin); testnet default.

## Progress checklist
- [ ] DID:ION create/resolve bound to principal (hosted resolver)
- [ ] ERC-721 mint flow (user-triggered + approval + cost surface) via MCP
- [ ] Autonomous-mint hard block
- [ ] Mint recorded in C5 as `reversible=false`
- [ ] testnet-first config; mainnet opt-in
- [ ] tests (unit + irreversibility + E2E) green

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |

## Cloud-agent prompt
> **[Wave 2 — start after FG-01 + FG-11 + FG-12 merge]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-02`). Implement **per-user DID:ION identity** (bound to `Principal.user_id`, hosted ION resolver first, custodial keys with a documented non-custodial upgrade path) and **ERC-721 minting per digital asset**. Extend `optional-skills/blockchain/evm` and expose chain operations as an **MCP server** registered via FG-11 — do NOT add a new core tool or vendor a blockchain product under `plugins/` (per `AGENTS.md`). Minting is the **explicit exception to undo (D6): it MUST be user-initiated AND user-approved (contract C6), and is recorded in the change log (contract C5) with `reversible=false` so FG-12 undo refuses it** — block agent-autonomous mint at the policy layer (test it). **Testnet/L2 by default**; mainnet only on explicit config + per-mint approval with gas/cost surfaced first. Add unit + **irreversibility** + autonomous-mint-blocked + E2E (mocked chain/resolver) tests; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc.
