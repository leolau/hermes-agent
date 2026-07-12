# Parallel Devin cloud-agent prompts

One prompt per feature group. **Launch order = waves** (see
[`README.md` §5](./README.md)). Do **not** start an FG until every FG it is
*blocked by* has merged.

**Phase 1 (FG-01–13):**
- **Wave 0 (foundations, merge first):** FG-13, FG-01, FG-05
- **Wave 1:** FG-03, FG-04, FG-06, FG-11, FG-12
- **Wave 2:** FG-07, FG-08, FG-10
- **Wave 3:** FG-09

**Phase 2 (FG-14–19, reqs 14.0–19.0 — start after Phase-1 `develop` merges):**
- **Wave A (foundations, publish contracts first):** FG-14 (C7), FG-16 (C8)
- **Wave B:** FG-18 (C9), FG-17a (dashboard parity port), FG-15
- **Wave C:** FG-19, FG-17b (dashboard new panels)

- **ON HOLD (not scheduled):** FG-02 (blockchain) — paused per Leo; do not launch until the owner resumes it. No downstream dependents.

Each agent: own branch off `develop`; edit **only its own** `feature-groups/FG-XX-*.md`
(progress checklist + audit log); keep `tests/plan_baseline/` green; add its own
FG tests; run `scripts/run_tests.sh`, `ruff`, `ty` (and web lint/typecheck if it
touches `web/`); open a PR linking its FG doc. The full, always-authoritative
prompt for each FG lives at the bottom of that FG's doc under **"Cloud-agent
prompt"** — the entries below are copies for convenience.

**System testing:** an FG is not done until, after its code is developed, its
**"System testing (system-test box)"** checklist (in its FG doc) passes on the
new hermes-systest ECS (`47.83.199.25`, the dedicated system-test host — never
the prod schema). See
[`README.md` §7.1](./README.md). Coordinate that deploy/run with Leo.

---

## Wave 0

### FG-13 — dev/prod mode + datastore router (C3)
See [`feature-groups/FG-13-dev-prod-mode.md`](./feature-groups/FG-13-dev-prod-mode.md) → *Cloud-agent prompt*.

### FG-01 — multi-user access (C1, C2)
See [`feature-groups/FG-01-multi-user-access.md`](./feature-groups/FG-01-multi-user-access.md) → *Cloud-agent prompt*.

### FG-05 — embedding memory + concurrency
See [`feature-groups/FG-05-embedding-memory-concurrency.md`](./feature-groups/FG-05-embedding-memory-concurrency.md) → *Cloud-agent prompt*.

## Wave 1

### FG-03 — multi-channel redesign (C4)
See [`feature-groups/FG-03-multi-channel-redesign.md`](./feature-groups/FG-03-multi-channel-redesign.md) → *Cloud-agent prompt*.

### FG-04 — goals + priority + measurability
See [`feature-groups/FG-04-goals-priority-measurability.md`](./feature-groups/FG-04-goals-priority-measurability.md) → *Cloud-agent prompt*.

### FG-06 — task discovery + progress
See [`feature-groups/FG-06-task-discovery-progress.md`](./feature-groups/FG-06-task-discovery-progress.md) → *Cloud-agent prompt*.

### FG-11 — agent comms MCP
See [`feature-groups/FG-11-agent-comms-mcp.md`](./feature-groups/FG-11-agent-comms-mcp.md) → *Cloud-agent prompt*.

### FG-12 — change management (C5, C6)
See [`feature-groups/FG-12-change-management.md`](./feature-groups/FG-12-change-management.md) → *Cloud-agent prompt*.

## Wave 2

### FG-07 — tools creation + dashboard
See [`feature-groups/FG-07-tools-creation-dashboard.md`](./feature-groups/FG-07-tools-creation-dashboard.md) → *Cloud-agent prompt*.

### FG-08 — OSS remote + in-house
See [`feature-groups/FG-08-oss-copy-mcp.md`](./feature-groups/FG-08-oss-copy-mcp.md) → *Cloud-agent prompt*.

### FG-10 — human comms (Telegram + web)
See [`feature-groups/FG-10-human-comms.md`](./feature-groups/FG-10-human-comms.md) → *Cloud-agent prompt*.


## Wave 3

### FG-09 — goal management integration
See [`feature-groups/FG-09-goal-management.md`](./feature-groups/FG-09-goal-management.md) → *Cloud-agent prompt*.

---

# Phase 2 (reqs 14.0–19.0)

## Wave A

### FG-14 — Core/Customizable boundary (C7)
See [`feature-groups/FG-14-core-customizable-boundary.md`](./feature-groups/FG-14-core-customizable-boundary.md) → *Cloud-agent prompt*.

### FG-16 — action tracking & traceability (C8)
See [`feature-groups/FG-16-action-tracking-traceability.md`](./feature-groups/FG-16-action-tracking-traceability.md) → *Cloud-agent prompt*.

## Wave B

### FG-18 — GTS Centre (C9)
See [`feature-groups/FG-18-gts-centre.md`](./feature-groups/FG-18-gts-centre.md) → *Cloud-agent prompt*.

### FG-17 — dashboard → Next.js + embedded Telegram + agent webview
See [`feature-groups/FG-17-dashboard-nextjs-face.md`](./feature-groups/FG-17-dashboard-nextjs-face.md) → *Cloud-agent prompt*. (17a parity port in Wave B; 17b new panels in Wave C.)

### FG-15 — easy onboarding
See [`feature-groups/FG-15-easy-onboarding.md`](./feature-groups/FG-15-easy-onboarding.md) → *Cloud-agent prompt*.

## Wave C

### FG-19 — per-user GTS isolation + cross-user assignment
See [`feature-groups/FG-19-gts-per-user-isolation-assignment.md`](./feature-groups/FG-19-gts-per-user-isolation-assignment.md) → *Cloud-agent prompt*.

---

## Orchestration note (automatic launch)
The orchestrator session can spawn these as **child sessions wave-by-wave**:
launch Wave 0 (3 agents) → wait for their PRs to merge (contracts C1–C6 land) →
launch Wave 1 (5 agents) → Wave 2 (3 agents) → Wave 3 (1 agent). Never launch
all at once — Waves 1–3 build on Wave-0 contracts and would collide on the
god-files otherwise. **FG-02 (blockchain) is on hold** and is not part of this
sequence until the owner resumes it.

**Phase 2:** after Phase-1 `develop` is merged, run the same pattern — Wave A
(FG-14, FG-16) publishes contracts C7/C8 first (plus FG-17a's parity port can
start in parallel), then Wave B (FG-18, FG-15 + FG-17a), then Wave C (FG-19,
FG-17b). Publish C7/C8/C9 as small interface PRs before Wave-B/C consumers to
avoid god-file collisions. FG-17 must re-run the FG-07/FG-10 system tests.
