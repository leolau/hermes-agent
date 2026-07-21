# FG-16 — Action tracking & traceability

**Wave:** A (Phase-2 foundation) · **Owner agent:** devin:eaf2cdff · **Status:** Implementation complete; system test pending

## Summary
Make **every interaction traceable** — the second most important property after
security (Req 16.0). Beyond the existing **cost tracking** and the FG-12 change
log (which records *mutations* only), add a unified **append-only interaction
trace**: every inbound message, agent turn, tool call, outbound reply, approval,
change, and cost record is written to a side ledger and joined by a single
**correlation `trace_id`**, so any interaction can be reconstructed end-to-end.
The trace is **cache-safe** (never fed back into the model prompt),
**access-scoped** (you see your own; owner sees all), and **retention-capped**
so it fits the box.

## Decisions applied
- **D11 — Every interaction is traced end-to-end via one `trace_id`; the trace is observability-only (cache-safe), RLS-scoped, and retention-capped.**
- C1/C2 (actor + scope; owner sees all), C5 (change events are one trace kind), C8 (this FG publishes the interaction-trace contract), footprint ladder (a sink/middleware, not a new core tool), prompt-cache sacred (side-channel only).

## Reuse map
- `hermes_logging.py` (`agent.log`/`gateway.log`/`errors.log`, profile-aware) — the existing structured-logging seam; the trace sink hooks the same events.
- `hermes_state.py` SessionDB — already persists turns/transcripts; trace references session + turn ids rather than duplicating content where possible.
- cost-tracker MCP (`/opt/data/mcp_cost_tracker.py` pattern) — cost rows fold into the trace as a `cost` kind.
- `hermes_cli/changes.py` (C5, FG-12) — change events become `change` trace rows (linked by `trace_id`).
- `gateway/run.py` inbound chokepoint (`_handle_message_with_agent`) + `run_agent.py` tool-call loop — the natural emit points for inbound/turn/tool/outbound spans.
- `plugins/observability/` — existing metrics/traces/logs plugin; reuse rather than add a parallel system.

## Design / approach
1. **Contract C8 — Interaction trace.** Append-only
   `interactions(id, trace_id, parent_id|null, ts, actor_user_id, session_key,
   platform, kind ∈ {inbound, turn, tool_call, tool_result, outbound, approval,
   change, cost, error, core_denied}, ref (session/turn/tool/change/cost id),
   summary, payload_ref|null, mode)`. One `trace_id` per originating interaction
   flows inbound → turn → tool calls → outbound (and any change/cost/approval it
   causes), forming a reconstructable tree via `parent_id`.
2. **`trace_id` propagation.** Minted at the inbound chokepoint and carried
   through the turn/tool loop and into C5 change rows + cost rows, so cost +
   changes + messages all join on one id. (Additive to `SessionSource`/context;
   byte-stable for existing callers when unset.)
3. **Cache-safe by construction.** The trace is written to the ledger/logs
   **only**; it is **never** injected into the system prompt or conversation
   (that would break prompt caching — a hard core rule). Nothing reads the trace
   back into a live turn.
4. **Access-scoped (C2).** Trace rows carry `actor_user_id` + scope; Postgres
   RLS ensures a user sees only their own traces, owner sees all. Sensitive
   payloads are stored by reference / redactable, not inlined where avoidable.
5. **Retention/rollup.** Configurable in `config.yaml`
   (`action_tracking: {retention_days, rollup, sample}`): keep full detail for
   N days, then summarize/archive older rows so the 4/16 box doesn't fill; an
   optional sampling knob for high-volume tool spans.
6. **Trace view (dashboard).** FG-17 renders a per-interaction timeline
   (inbound → tools → outbound + linked cost/changes/approvals) and a
   per-user/owner audit browser. This FG exposes the query API.

## Data model (Supabase `app_*` + reuse)
- `interactions` (C8) in `app_*`, scoped by C2, mode-tagged, `trace_id`-indexed.
- Reuses SessionDB (turn/transcript), C5 `changes`, and cost rows — trace rows **reference** them (no wholesale duplication).

## Dev/Prod + Supabase
Trace carries `mode`; channels (prod) trace to `app_prod`, local/dev to dev.
Retention policy applies per schema. Routed via C3.

## Testing requirements
- Unit: `trace_id` minting/propagation across turn→tool→change→cost; row-kind coverage; retention/rollup + sampling logic.
- **Cache-safety test (required):** enabling tracing does **not** alter the system prompt / message sequence (prompt bytes identical with tracing on vs off) — no mid-conversation injection.
- Negative access: a user cannot read another user's private trace; owner can (app layer **and** Postgres RLS).
- E2E: one real inbound → assert a single `trace_id` links inbound + turn + ≥1 tool_call + outbound (+ any change/cost); reconstruct the tree; retention prunes/rolls up old rows.
- Baseline green.

## System testing (system-test box)
**Required after this FG's development completes** (part of DoD), on the new ECS (`hermes-systest`, 4/16, EIP `47.83.199.25`) against **staging** (`app_dev`) (**never prod**). See README §7.1. Checklist:
- Drive a real interaction through a live channel; confirm one `trace_id` reconstructs the full inbound→turn→tool→outbound timeline with linked cost + any change rows.
- Confirm tracing is **cache-safe** on the live agent (no prompt-cache invalidation attributable to tracing) and **access-scoped** via real RLS (member sees own, owner sees all).
- Confirm retention/rollup runs and bounds table growth on the box.
- **Gate:** not complete/promotable until this checklist passes.

## Dependencies
- **Blocked by:** FG-01 (C1/C2), FG-12 (C5), FG-13 (C3). Complements FG-14 (traces `core_denied`).
- **Blocks:** FG-17 (trace view render). Publishes contract **C8**.

## Definition of Done
Tests green (incl. cache-safety + negative access + RLS) + baseline green + `ruff`/`ty` clean; C8 published; one `trace_id` joins messages+tools+changes+cost; observability-only (no prompt injection); retention/rollup + RLS scoping working; **ECS system test green**.

## Progress checklist
- [x] C8 `interactions` ledger + emit points (inbound/turn/tool/outbound/approval/change/cost/error); the additive `core_denied` kind is published and covered, with runtime emission owned by the FG-14 guard when that seam lands
- [x] `trace_id` minting + propagation into C5 changes + cost rows
- [x] Cache-safe guarantee (side-channel only; prompt bytes unchanged)
- [x] Retention/rollup/sampling (config.yaml) + RLS access scoping (C2)
- [x] Query API for the dashboard trace view (FG-17 renders)
- [x] tests (unit + cache-safety + negative + RLS + E2E) and `tests/plan_baseline/` green; the full-suite delta is clean against `develop`
- [ ] System test on the system-test ECS passed (see *System testing* section)

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-12 | 1 | devin:8cec0d47 (for Leo) | Created FG doc | Phase-2 req 16.0: action tracking (2nd only to security) — every interaction traceable, joined by trace_id |
| 2026-07-12 | 2 | devin:eaf2cdff (for Leo) | Implemented C8 ledger, gateway/turn/tool/change/cost propagation, C2/RLS query scope, retention/rollup/sampling, dashboard API, and cache/access/E2E coverage | Publish the smallest additive side-channel contract without changing prompts, conversation roles, the model tool surface, or outbound telemetry defaults; ECS validation and production promotion remain requester-owned gated steps |

## Cloud-agent prompt
> **[Phase-2 Wave A — start after Phase-1 develop is merged]** Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-16`). Publish contract **C8 = interaction trace**: append-only `interactions(id, trace_id, parent_id, ts, actor_user_id, session_key, platform, kind∈{inbound,turn,tool_call,tool_result,outbound,approval,change,cost,error,core_denied}, ref, summary, payload_ref, mode)` in Supabase `app_*` (C3-routed, C2-scoped). Mint one **`trace_id`** at the gateway inbound chokepoint (`gateway/run.py`) and propagate it through the `run_agent.py` turn/tool loop and into C5 change rows + cost rows so cost+changes+messages join on one id. **Reuse** `hermes_logging.py`, SessionDB, the cost-tracker, `hermes_cli/changes.py`, and `plugins/observability/` — do NOT build a parallel system. The trace is **observability-only**: it must be written to the side and **never injected into the system prompt or conversation** (prove prompt bytes are identical with tracing on vs off — prompt caching is sacred). Enforce **access scoping** via Postgres RLS (member sees own, owner sees all). Add **retention/rollup/sampling** in `config.yaml` (`action_tracking:`) to bound growth on the 4/16 box (no new non-secret env vars). Expose a query API for the FG-17 trace view. Follow `AGENTS.md` (footprint ladder — a sink/middleware, not a new core tool). Add unit + **cache-safety** + negative-access + RLS + real-path E2E tests (temp `HERMES_HOME` + throwaway Postgres); keep baseline green; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist passes** — coordinate with Leo.
