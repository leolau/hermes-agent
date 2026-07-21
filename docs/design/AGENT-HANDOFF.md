# Agent Hand-off Note — SUPERSEDED, see the master plan

> **⚠️ This note is historical.** The design effort it described has since grown
> into a full, structured **master plan**. Do **not** treat this file as the
> current status or source of truth.
>
> **Source of truth (read these first):**
> - **[`master-plan/README.md`](./master-plan/README.md)** — locked decisions
>   (D1–D15), cross-cutting contracts (C1–C9), the FG-01–19 index, the
>   dependency waves, the system-test/promotion gates, and the append-only
>   changelog (§9) that records what has actually landed.
> - **[`master-plan/feature-groups/`](./master-plan/feature-groups/)** — the
>   per-feature-group docs (scope, checklist, audit log, system-test gate).
>
> The old design tracks map onto the plan as follows:
> - **Design #1 (frozen)** → [`architecture-design-number-one.md`](./architecture-design-number-one.md) (read-only history).
> - **Design #2 (blank-sheet redesign)** → **became the master plan**. Its once-open
>   memory-consistency question is now **resolved as decision D2 (hybrid memory)**:
>   durable semantic memory stays a frozen `MEMORY.md`/`USER.md` snapshot, while
>   volatile coordination state + embeddings live in the queryable app datastore.
>   The `account_id` / session-identity work is contract **C4 + D7**; the
>   one-brain multi-channel wiring is **FG-03**. No separate
>   `architecture-design-number-two.md` was written — the plan replaced it.

---

## Current status (2026-07, summary — see README §9 for the authority)

- **Phase 1 (FG-01–13)** — largely built and system-tested; live **read-only**
  channel validation done (Telegram full round-trip; WhatsApp + Gmail IMAP
  read-only). Auto-reply/SMTP send NOT tested; **prod not promoted** (owner
  deferred).
- **Phase 2 (FG-14–19)** — all merged into `develop`, including the Wave-C
  integration:
  - **FG-19** (per-user GTS isolation + per-item cross-user assignment) — merged (PR #35).
  - **FG-17b** (dashboard new panels: Core-area view, GTS Centre with FG-19
    assignment, onboarding, consent-gated agent webview, native Telegram pane) —
    merged (PR #34).
- **Remaining, owner-gated (not code):**
  - Per-FG **ECS system-test-box** checklists (`hermes-systest`, EIP
    `47.83.199.25`) — need ≥2 real users + a live channel; owner-run.
  - **Prod promotion** (`app_prod` + prod `state.db`).
  - **FG-03 live WhatsApp/email round-trip** + auto-reply/SMTP — pending channel
    creds (email = Gmail IMAP app-passwords; WhatsApp = QR bind).
  - **FG-02 (blockchain DID + ERC-721)** — ON HOLD; resumes only on explicit
    owner go-ahead. No downstream dependents.

---

## ★ Key design principle (unchanged, carried through the whole plan)

Every WhatsApp number, every email address, and every calendar is just a
different **incoming channel** on the same pattern. Once an event arrives it is
handled by **ONE shared agent infrastructure** — same skills, same memory, same
context: **one brain, one profile**, no per-channel/per-account silos. (In the
plan this is D1 multi-user-not-multi-tenant + FG-03 one-brain gateway.)

---

## Live system facts (still useful for operators)

- **Personal box:** Alibaba Cloud ECS, **cn-hongkong**, instance
  `i-j6camnt3ocwlmzajthil`, IP `8.217.86.90`. Docker container `hermes-agent`,
  `HERMES_HOME=/opt/data` (one unified profile).
- **System-test box:** a separate 4/16 ECS `hermes-systest`
  (`i-j6c81aisv2dd8mg17yle`, EIP `47.83.199.25`, 100 GB ESSD at `/opt/data`),
  which also hosts prod for now (`app_staging` vs `app_prod` schemas + separate
  SQLite cores via C3). See README §7.
- **Reaching a box from the agent VM:** no SSH key on file; use the `aliyun`
  CLI → ECS RunCommand (Cloud Assistant), creds in env
  (`ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`).
  Base64-encode a shell script, `RunCommand --Type RunShellScript
  --ContentEncoding Base64`, then poll `DescribeInvocationResults`; commands run
  `docker exec hermes-agent sh -lc '...'`.
- **Model:** `deepseek-reasoner` (provider `deepseek`) is `model.default`; every
  per-role/per-platform override is blank → all fall back to it. Only model key
  set is `DEEPSEEK_API_KEY`; `providers: {}` is empty. **Grok is NOT active** —
  `x_search.model: grok-4.20-reasoning` is a dormant shipped default (no xAI key,
  no provider), so X-search is inert.
- **`alibabacloud` MCP server currently fails to init** — infra used the `aliyun`
  CLI instead (README §8).

---

## Constraints to respect (from `AGENTS.md`)

- **Prompt-cache safety:** system prompt byte-stable within a conversation;
  strict role alternation; never inject a synthetic user message mid-loop.
- **Footprint Ladder:** extend existing → CLI+skill → service-gated tool →
  plugin → MCP → (last resort) new core tool. Don't grow the core waist casually.
- **`.env` = secrets only;** all behavioral config in `config.yaml`.
- **Core is immutable to the runtime agent** (C7, fail-closed); every
  interaction is traced end-to-end (C8 `trace_id`).
- Subagents can't clarify/memory/recurse and run `skip_memory=True` — pass
  context in the briefing.

---

## Files

- [`master-plan/`](./master-plan/) — **the current plan (source of truth).**
- [`SESSION-HANDOFF-2026-07-prod-cutover.md`](./SESSION-HANDOFF-2026-07-prod-cutover.md)
  — **operational** hand-off: the live ai-prentice-4-all prod cutover (public URL, hosts,
  DNS/TLS, Telegram gateway, promotion, rollback runbook, open follow-ups).
- [`architecture-design-number-one.md`](./architecture-design-number-one.md) — frozen design #1 (read-only history).
- `AGENT-HANDOFF.md` — this note (now just a pointer to the plan).
- `../WHATSAPP_IMPLEMENTATION.md`, `../EMAIL_IMPLEMENTATION.md`,
  `../CALENDAR_IMPLEMENTATION.md` — the standalone channel implementations the
  one-brain wiring (FG-03) connects to the agent loop.
