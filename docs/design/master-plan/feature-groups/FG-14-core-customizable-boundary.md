# FG-14 — Core vs Customizable boundary + protection

**Wave:** A (Phase-2 foundation) · **Owner agent:** _unassigned_ · **Status:** Implemented (C7 Core write-guard `agent/core_boundary.py` + `core_manifest.yaml`) — merged to `develop` (PR #27); ECS system-test remains owner-gated

## Summary
Split the system into two explicit areas — **Core** (fixed system machinery) and
**Customizable** (user/agent-extendable) — and make the split **enforced at
runtime**, not just conventional. The **runtime LLM agent** (the DeepSeek model
driven by a user's prompt) **must never be able to modify Core**, no matter what
a user asks it to do; this prevents a user from talking the agent into breaking
the system. All changes to the **Customizable** area are tracked (reusing the
FG-12 change log) so the system can **self-improve over time** with a full,
reversible history. (Req 14.0; note 18.0: Core is also un-editable by end
users — only human developers change Core via the repo/PR.)

## Decisions applied
- **D10 — Core is immutable to the runtime agent AND to end users; changeable only by human developers via the repo/PR.** Enforced by a hard runtime write-guard, not convention.
- Footprint ladder: the guard lives at the existing file/terminal write chokepoint (a `check_fn`-style gate), not a new core tool.
- C5 (every Customizable-area change emits a change-event), C6 (any escalation path is approval-gated), C2 (change history scoped; owner sees all).

## Reuse map
- `AGENTS.md` "narrow waist (core) vs edges" — this FG turns that doctrine into an enforced, testable boundary; the **core manifest below is the machine-readable form of it**.
- `tools/` write path + `tools/environments/` terminal backends + file-write tool — the single chokepoint where the guard is applied (deny writes whose resolved path is inside Core).
- `tools/approval.py` / `write_approval.py` (C6) — for the (default-off) escalation path if we ever allow a human-approved core proposal.
- `hermes_cli/changes.py` (C5, FG-12) — records every Customizable-area edit.
- `tools/checkpoint_manager.py` — git-shadow snapshots already exist; Core files are simply out of the agent's write scope.

## Design / approach
1. **Contract C7 — Core boundary.** A repo-committed **`core_manifest.yaml`**
   enumerates Core paths (globs) — e.g. `run_agent.py`, `model_tools.py`,
   `toolsets.py`, `cli.py`, `hermes_state.py`, `gateway/**` core, the GTS Centre
   engine (FG-18), the change-management engine (FG-12), the boundary guard
   itself, and `core_manifest.yaml`. Everything else (`plugins/**`,
   `skills/**`, user-created tools, `config.yaml` behavioural settings,
   Supabase `app_*` data) is **Customizable**.
2. **Runtime write-guard (hard block).** At the file/terminal write chokepoint,
   resolve the target path; if it falls under a Core glob, **refuse the write**
   with a clear message and emit a C5 "core-write-denied" audit event (C8
   trace). This applies to the LLM agent's tools only — it does **not** touch
   the human developer workflow (git/PR, `hermes update`), which operates
   outside the agent's tool sandbox. The guard is **fail-closed**: if the
   manifest can't be read, treat unknown paths under known-core roots as Core.
3. **No user override.** There is no config flag or prompt that lets an end user
   or the agent disable the guard (that would defeat 14.0). The manifest is
   itself Core (self-protecting). Changing Core = a human PR.
4. **Customizable-change tracking for self-improvement.** Every write the agent
   *is* allowed to make (Customizable area) emits a C5 change-event with
   before/after + `backup_ref`, so the Customizable area has a complete,
   reversible, queryable history the system can learn from and roll back.
5. **Escalation path (default OFF, documented for the future).** If we ever want
   the agent to *propose* a Core change, it may only write a proposal artifact
   in the Customizable area (never Core) that a human reviews and lands via PR.
   No autonomous Core edit, ever.

## Data model
- `core_manifest.yaml` (repo-committed, Core) — `{core_globs: [...], notes}`.
- Reuses C5 `changes` (FG-12) for Customizable edits + `core-write-denied` audit rows; reuses C8 trace (FG-16) for the denied-attempt trace.

## Dev/Prod + Supabase
Guard is mode-agnostic (Core is Core in dev and prod). Denied-write audit rows
carry `mode` via C5/C8. No new schema beyond the manifest + reused change log.

## Testing requirements
- Unit: path-resolution classifier (Core vs Customizable) incl. symlink/`..`/absolute-path escape attempts; fail-closed when manifest unreadable.
- **Security test (required):** the agent's file/terminal write to a Core path is **refused** (and audited); a write to a Customizable path succeeds and emits C5.
- No-bypass test: there is no config/env/prompt that disables the guard.
- E2E: real agent turn instructed (via prompt) to "edit `run_agent.py`" → guard denies, trace + audit recorded, system unchanged; "edit a skill/config" → allowed + logged.
- Baseline green.

## System testing (system-test box)
**Required after this FG's development completes** (part of DoD), on the new ECS (`hermes-systest`, `i-j6c81aisv2dd8mg17yle`, 4/16, cn-hongkong-b, EIP `47.83.199.25`) against **staging** (`app_dev`) + staging SQLite core (**never prod**). See README §7.1. Checklist:
- On the live box, prompt the running DeepSeek agent to modify a Core file (several phrasings) → every attempt is refused and audited; Core bytes unchanged.
- Prompt it to modify a skill/plugin/config (Customizable) → succeeds and emits a C5 change-event visible in the change log.
- Confirm no user-reachable switch disables the guard.
- **Gate:** not complete/promotable until this checklist passes (on top of the per-PR gate).

## Dependencies
- **Blocked by:** FG-12 (C5), FG-01 (C2 actor/scope). Consumes FG-16 (C8) when present (trace of denials) but degrades gracefully without it.
- **Blocks:** nothing hard; FG-18 marks the GTS engine + user-owned evaluation methods as Core; FG-17 renders the Core-area view.
- Publishes contract **C7**.

## Definition of Done
Tests green (incl. security + no-bypass) + baseline green + `ruff`/`ty` clean; `core_manifest.yaml` committed; runtime guard hard-blocks agent writes to Core (fail-closed, no user override) and audits them; Customizable edits emit C5; **ECS system test green**.

## Progress checklist
- [x] `core_manifest.yaml` + path classifier (fail-closed, escape-safe)
- [x] Runtime write-guard at the file/terminal chokepoint (agent-only; hard block + audit)
- [x] No-bypass guarantee (manifest self-protected; no disable switch)
- [x] Customizable-edit change tracking (C5) for self-improvement history
- [x] tests (unit + security + no-bypass + E2E) green
- [ ] System test on the system-test ECS passed (see *System testing* section) — separate gated step owned by Leo; not run by the agent

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-12 | 1 | devin:8cec0d47 (for Leo) | Created FG doc | Phase-2 req 14.0: enforce Core/Customizable boundary; runtime agent must never edit Core |
| 2026-07-12 | 2 | devin:ce62671e (for Leo) | Implemented C7: `core_manifest.yaml` + `agent/core_boundary.py` guard wired into the agent file-write chokepoint (`tools/file_operations.py` write/patch/delete/move); fail-closed + escape-safe; denials emit C5 audit + C8 `core_denied` trace; unit + security + no-bypass + real-path E2E tests added | Publish C7 as a small additive interface; runtime agent hard-blocked from Core with no override, human dev/git/`hermes update` path unchanged. ECS system test remains a separate gated step owned by Leo. |

## Cloud-agent prompt
> **[Phase-2 Wave A — start after Phase-1 develop is merged]** Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-14`). Publish contract **C7 = Core/Customizable boundary**: commit a machine-readable **`core_manifest.yaml`** (globs for Core: `run_agent.py`, `model_tools.py`, `toolsets.py`, `cli.py`, `hermes_state.py`, core `gateway/**`, the FG-18 GTS engine, the FG-12 change engine, the guard, and the manifest itself; everything else is Customizable). Implement a **hard runtime write-guard** at the agent's file/terminal write chokepoint that **refuses any write whose resolved path is Core** (fail-closed, escape-safe against `..`/symlink/absolute paths) and emits a C5 audit + C8 trace. This applies to the **runtime LLM agent only** — do NOT touch the human dev/git/`hermes update` path. There must be **no config/env/prompt that disables the guard** (the manifest is self-protecting). Every allowed (Customizable) write emits a C5 change-event for self-improvement history. Follow `AGENTS.md` (footprint ladder — a `check_fn`-style gate, NOT a new core tool; `.env` = secrets only). Add unit + **security** (agent write to Core refused+audited) + no-bypass + real-path E2E tests (temp `HERMES_HOME`); keep `tests/plan_baseline/` green; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist passes** — coordinate with Leo.
