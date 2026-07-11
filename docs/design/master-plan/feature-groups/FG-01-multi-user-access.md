# FG-01 — Multi-users with access rights; single transferable owner

**Wave:** 0 (foundation) · **Owner agent:** _unassigned_ · **Status:** Not started

## Summary
Turn the single-owner personal agent into a **multi-user** system with **access
rights**, over **one shared brain** (NOT multi-tenant profiles). Three-tier
visibility: **shared** org knowledge, **per-user private** memory/skills, and an
**owner** who can see everything. Exactly one **owner**, but ownership is
**transferable** (approval-gated).

## Decisions applied
- D1 (three-tier visibility; multi-user not multi-tenant), D6 (owner ties to per-user blockchain identity in FG-02).

## Reuse map
- `gateway/authz_mixin.py` — inbound authorization (allowlist / `dm_policy` / `group_policy`); extend into a principal/role model, don't replace.
- `gateway/pairing.py` — owner-approved pairing codes + `*_ALLOWED_USERS`; reuse for enrolling new users.
- `hermes_cli/dashboard_auth/`, `gateway/slash_access.py` — dashboard/slash access gates.
- **Supabase GoTrue (auth) + Postgres RLS** — the enforcement backbone for users/roles/visibility.

## Design / approach
1. **Contract C1 — Principal/identity.** `Principal{user_id, display, role ∈
   {owner, admin, member, viewer}, channels[], created_at}`. A
   `resolve_principal(SessionSource) -> Principal` seam in the gateway maps an
   inbound channel identity to a system user (via pairing/enrolment). Backed by
   GoTrue; cached per session.
2. **Contract C2 — Visibility/scoping.** Every scoped row carries
   `owner_user_id` + `visibility ∈ {shared, private:<user_id>}`. Helpers
   `can_read(principal, row)` and `scope_filter(principal) -> SQL/predicate`
   used by memory (FG-05), skills, goals (FG-04), tasks (FG-06), tools (FG-07),
   assets (FG-02). **Owner bypasses the filter** (sees all). Enforced primarily
   by **Postgres RLS** so it can't be bypassed at the app layer; SQLite-core
   rows that need scoping carry the same columns and use `scope_filter`.
3. **Ownership transfer.** `hermes owner transfer <user_id>` (CLI+skill) →
   approval (C6) by current owner → reassigns the single `owner` role
   atomically; emits a change-event (C5). Invariant: exactly one owner always.
4. **Enrolment.** Owner/admin approves a pairing code → creates a `Principal`
   with default role `member`. Per-channel identities link to one Principal.

## Data model (Supabase `app_*`)
- `principals(user_id PK, display, role, created_at, ...)` — CHECK exactly-one-owner via partial unique index.
- `channel_identities(platform, channel_user_id, user_id FK, ...)` — maps inbound identity → principal.
- RLS policies: shared rows readable by all; `private:<u>` readable only by `u`; owner role bypasses.

## Dev/Prod + Supabase
Principals/identities live in **prod** (auth is prod). RLS policies mirrored to
`app_dev` for testing. No dev users concept beyond schema parity.

## Testing requirements
- Unit: role model, exactly-one-owner invariant, transfer atomicity.
- **Negative access tests (required):** member A cannot read `private:B`; owner CAN read `private:B`; shared readable by all.
- E2E: enrol via pairing → member; owner transfer end-to-end (approval + change-event + single-owner invariant holds).
- RLS enforcement test hitting Postgres directly (not just app-layer).

## System testing (system-test box)
**Required step after this FG's development completes** (part of its Definition of Done), on top of the per-PR unit/E2E + baseline gate: deploy this FG to the new ai-prentice ECS (`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, 4/16, cn-hongkong-b, EIP `47.83.199.25`) — the dedicated **system-test host** — and exercise it end-to-end on the real stack against a **staging** Supabase schema (`app_staging`) + staging SQLite core (**never prod**). See README §7.1. Acceptance checklist:
- Enrol ≥3 real test users via pairing on the deployed box; confirm roles (owner/admin/member/viewer) resolve through the live gateway.
- Against **real Postgres RLS + GoTrue**: member A cannot read `private:B`; shared org knowledge readable by all; owner sees everything.
- Ownership transfer end-to-end on the box: exactly-one-owner invariant holds; change-event recorded.
- **Gate:** this FG is not complete/promotable until this ECS checklist passes (on top of the per-PR gate).

## Dependencies
- **Blocked by:** FG-13 (C3 datastore router) for the Supabase app store.
- **Blocks:** FG-02, FG-05 (scoping), FG-07, FG-09, FG-10, FG-11 (auth), FG-12 (actor).
- Publishes contracts **C1, C2**.

## Definition of Done
Tests green (incl. negative access + RLS) + baseline green + `ruff`/`ty` clean; **ECS system test green**; C1/C2 documented as typed interfaces; ownership-transfer E2E works.

## Progress checklist
- [x] Principal/role model + GoTrue integration (C1)
- [x] Visibility columns + `can_read`/`scope_filter` + RLS policies (C2)
- [x] Enrolment via pairing → principal
- [x] `hermes owner transfer` (approval + single-owner invariant)
- [x] tests (unit + negative access + RLS + E2E) green
- [ ] System test on the system-test ECS passed (see *System testing* section)

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |
| 2026-07-11 | 2 | devin:8cec0d47 | Added System testing (system-test box) section as a per-FG DoD step | Leo: new 4/16 ECS = system-test host (+ prod for now), run after each FG's development |
| 2026-07-11 | 3 | devin:a4e646c3 | Implemented C1 (`Principal`/roles + `resolve_principal` seam + pairing enrolment, GoTrue-subject-backed principals) and C2 (`shared`/`private:<user>` visibility + `can_read`/`scope_filter` app filter + Postgres RLS) in `hermes_cli/access.py`; approval-gated `hermes owner transfer` (single-owner invariant + C5 change-event) in `hermes_cli/owner.py` + `transfer-ownership` skill; unit + throwaway-Postgres E2E incl. negative-access + direct-RLS tests. Consumes FG-13 C3 `get_store`. | FG-01 C1+C2 delivery; DB-enforced access boundary over the single shared brain |

## Cloud-agent prompt
> **[Wave 0 — start after FG-13 C3 merges]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-01`). Implement the **multi-user access model over the single shared brain** (NOT multi-tenant profiles): principal/role model `{owner, admin, member, viewer}` (contract C1) backed by self-hosted **Supabase GoTrue**, a `resolve_principal(SessionSource)` gateway seam reusing `gateway/pairing.py` + `gateway/authz_mixin.py`; three-tier visibility `shared | private:<user> | owner-sees-all` (contract C2) with helpers `can_read`/`scope_filter` enforced by **Postgres RLS**; and an approval-gated `hermes owner transfer` keeping the **exactly-one-owner** invariant, emitting a change-event (C5). Do NOT create per-user profiles — one `HERMES_HOME`. Follow `AGENTS.md` (footprint ladder, no new core tools, `.env` = secrets only). Add unit + **negative access tests** + RLS enforcement test + E2E, all against a temp `HERMES_HOME` + throwaway Postgres schema; keep `tests/plan_baseline/` green; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist (in this doc) passes** — coordinate that deploy/run with Leo.
