# Hermes Skills-Focus Triage: Self-Improving, Human-in-the-Loop, Per-Focus MCP

**Author:** Devin (plan only — no code changes yet)
**Target deployment:** `hermes-agent` Docker container on ECS `ai-prentice-agentdoc` (8.217.86.90), `HERMES_HOME=/opt/data`
**Repo studied:** `leolau/hermes-agent` @ `10499e75a`
**Date:** 2026-07-05

---

## ★ KEY DESIGN PRINCIPLE (overrides everything else) ★

**Every WhatsApp number, every email address, and every calendar is just a different INCOMING CHANNEL — they all follow the same channel design pattern. Once a message/event comes in, it is handled by ONE shared agent infrastructure: the same set of skills, the same memory, and the same context.**

- Channels are interchangeable *inputs*; there is exactly **one brain** behind them.
- No per-channel and no per-account silos of skills/memory/context. Adding another WhatsApp number, email, or calendar = adding another channel into the *same* infrastructure — never a new profile, never a separate skill/memory store.
- Concretely: **one profile, `HERMES_HOME=/opt/data`**, shared by all channels (see §0.5, §1.5). Any design choice that would split skills, memory, focuses, or learned rules across channels/accounts is wrong by definition.

This principle takes precedence over any other detail in this document.

---

## 0. Executive summary

You want three things layered onto the existing WhatsApp + email triage pipeline:

1. **Skills-focus routing + self-improvement** — each incoming message is classified into a *focus* (accounting, marketing, business-leads, personal, …) and then processed by a focus specialist that gets better over time.
2. **Telegram human-in-the-loop** — when the pipeline is unsure or needs approval, it asks you on Telegram and waits.
3. **Per-focus MCP + self-improving MCP creation** — each focus has its own MCP server for querying/acting on its data, and the system can grow new MCP capabilities on its own (with your approval).

**The key architectural decision in this plan:** the current pipeline (`triage_agent.py`, `email_triage_agent.py`) is a *bespoke reimplementation* that bypasses Hermes's native machinery for exactly these three problems. Hermes already ships:

| Your goal | Hermes primitive that already exists | File |
|-----------|--------------------------------------|------|
| Self-improving skills | `skill_manage` tool + `/learn` prompt builder + **background review fork** + **curator** (auto-consolidate/prune agent-created skills) | `tools/skill_manager_tool.py`, `agent/learn_prompt.py`, `agent/background_review.py`, `tools/skill_provenance.py` |
| Focus specialists | `delegate_task` subagents (isolated context, restricted toolset, inherit MCP) | `tools/delegate_tool.py`, `tools/async_delegation.py` |
| Human-in-the-loop over Telegram | `clarify` tool + gateway clarify (inline-keyboard buttons, text fallback, blocking with timeout) + `approval` | `tools/clarify_gateway.py`, `tools/clarify_tool.py`, `tools/approval.py` |
| Per-focus MCP + new MCP capabilities | `hermes mcp add/install/catalog/serve`, MCP catalog manifests, `inherit_mcp_toolsets` for subagents | `hermes_cli/mcp_config.py`, `hermes_cli/mcp_catalog.py`, `tools/mcp_tool.py` |

So the plan is **bridge, don't reinvent**: keep the ingestion layer (WhatsApp bridges, IMAP poller, batcher, SQLite) as-is, but move the *reasoning / self-improvement / intervention / MCP* layers onto Hermes-native primitives. This aligns with the repo's own "Footprint Ladder" (AGENTS.md): prefer CLI+skill and MCP over new core tools, `.env` for secrets only, never break the main agent's prompt cache.

---

## 0.5 Foundational principle — ONE unified context across all channels

> **Decision (from you):** every incoming channel — **2 WhatsApp numbers + 3 email accounts + 3 calendars** — must operate as **one assistant with a single shared context: the same skills, the same memory, the same focuses, the same learned rules.** No siloed per-account brains.

This corrects an earlier suggestion. Skills and memory are scoped to a **profile** (`HERMES_HOME`), *not* to a channel or a Google account. So the rule for this whole design is:

- **Exactly one profile: `HERMES_HOME=/opt/data`.** All ingestion (WhatsApp, email, calendar), all skills (`/opt/data/skills/…` incl. every `focus-*`), all memory (`MEMORY.md` + memory store), the router, the focus registry, and every focus MCP live in this **one** profile. There is **no** `~/.hermes-acct1/2/3` split.
- **Multiple Google accounts do NOT require multiple profiles.** The only reason the earlier walkthrough suggested separate profiles was that the `google-workspace` skill's `setup.py` hard-codes a single token path (`HERMES_HOME/google_token.json`, `setup.py:42`) — so a naïve second `setup.py` run would *overwrite* the first account's token. The fix is to store **per-account tokens at distinct paths** and have the calendar ingestion read all three — exactly like the email pipeline **already** polls 3 Gmail accounts from **one** profile using 3 app passwords in `.env` (`email_poller.py`). Calendar becomes the same pattern.
- **Result:** a lead that arrives by WhatsApp, a contract that arrives by email, and a meeting on any of the 3 calendars all flow into the *same* triage brain, are routed by the *same* focus router, learn into the *same* `focus-*/learned/` skills, and are remembered in the *same* memory. Ask on Telegram "what's on today across everything?" and it answers from the unified store.

The multi-account calendar ingestion (how to get 3 calendars into this one profile) is detailed in **§1.5**.

---

## 1. How it works today (baseline, verified live)

```
WhatsApp #1/#2 (Baileys bridges :3000/:3001) ─┐
                                              ├─→ batcher.py (5s)  ─→ batches/*.json
Email x3 (IMAP poll 60s) ─→ email_poller.py ──┴─→ email_batcher.py (30s) ─→ batches/*.json
                                                        │
                            triage_agent.py / email_triage_agent.py  (watch batches/)
                                                        │  load_skills() reads /opt/data/skills/{whatsapp,email}-triage/*.md
                                                        │  one DeepSeek call → JSON {classification, tasks, notes, escalate}
                                                        ▼
                                            whatsapp_data.db  (15 tables)
                                                        │
                     escalation_pusher.py ─→ Telegram        digest_cron.py ─→ hourly Telegram digest
                     mcp_server.py :8650 / email_mcp_server.py  ─→ main Hermes agent queries
```

**What's real:** skills are hot-reloaded markdown, injected into the DeepSeek system prompt every batch (`load_skills()` in `triage_agent.py:59`). Editing a `.md` changes behavior on the next message.

**What's a gap:** `use_hermes_memory: true` is a **no-op** — the triage scripts never read `MEMORY.md`. `custom/` learned-skill dirs are **empty**. `telegram_callback_handler.py` only does contact merge/reject buttons — there is **no** correction→skill loop and **no** intervention path. There is one flat classification taxonomy, not a focus taxonomy. **Calendar is not ingested at all** (no poller, no token) — the third channel is missing.

---

## 1.5 Calendar ingestion — 3 Google accounts into the ONE profile

Goal: bring all 3 calendars into `HERMES_HOME=/opt/data` as a **third ingestion pipeline** that feeds the *same* batcher → router → focus specialists → SQLite → escalation/digest path as WhatsApp and email. This is a direct clone of the existing email pipeline's "N accounts, one profile" shape (`email_poller.py` polls 3 Gmail accounts from one profile using 3 app passwords in `.env`).

### 1.5.1 Multi-account OAuth without multiple profiles
The `google-workspace` `setup.py` writes one token at `HERMES_HOME/google_token.json` (`setup.py:42`), so it can't natively hold 3 accounts. Two clean options (recommend **A**):

- **(A) Per-account token files in the one profile.** Store `/opt/data/calendar-messages/tokens/{acct1,acct2,acct3}.json`. Generate each by running the OAuth flow with a **scratch `HERMES_HOME`** just for auth, then move the produced `google_token.json` to the per-account path. One shared OAuth client (`google_client_secret.json`) is reused for all three; each account authorizes once (calendar-only scope). The calendar poller then loads each token explicitly with `Credentials.from_authorized_user_file(<path>)` and iterates all three — never touching the profile's single-token path. **No new profile, no skill/memory split.**
- **(B) Google service account + domain-wide delegation.** Only works if the accounts are in a Workspace org you admin; overkill for mixed personal Gmail. Skip unless all three are Workspace-managed.

> Depends on §9 Q: are the 3 calendar accounts personal Gmail or Workspace-managed? (Determines A vs. B and the consent-screen/test-user setup.)

### 1.5.2 The calendar pipeline (mirrors email)
```
Google Calendar x3 ─→ calendar_poller.py (poll every 60–300s, per-account token)
                          │   fetch events changed since last sync token (incremental)
                          ▼
                     calendar_batcher.py (debounce) ─→ batches/*.json
                          ▼
                     (same router → focus specialist path as WhatsApp/email)
                          ▼
                     whatsapp_data.db  (new calendar_events / calendar_tasks tables in the SAME DB)
                          ▼
        escalation_pusher.py (e.g. meeting in <30 min, double-booking) + digest_cron.py (today's agenda)
```
- **Same shared SQLite** (`/opt/data/whatsapp-messages/whatsapp_data.db`) so calendar data sits alongside WhatsApp/email and any focus MCP can join across all three.
- **Same focus routing:** a calendar event is routed to a focus just like a message (e.g. a client meeting → business-leads; a tax deadline → accounting; a family event → personal).
- **Calendar-aware actions** (create/move/decline events) are outbound actions → **approval-gated by default** (§3.1), and can later be exposed via a `focus-*` MCP or the `google-workspace` tools.
- **`.env` holds only secrets** (the OAuth client + refresh tokens); poll intervals and per-account config go in a `calendar-messages/config.json`, mirroring the email pipeline.

### 1.5.3 Where this lands in the rollout
Calendar ingestion is **prerequisite plumbing**, independent of the focus/MCP work. It can be built early (right after P0) so that by the time focus specialists exist, all three channels already feed them. Added as **P0.5** in §5.

---

## 1.6 Conformance audit vs. the ★ KEY DESIGN PRINCIPLE (verified live)

Audited the **live** box (container `998beb639f03`, `HERMES_HOME=/opt/data`) on 2026-07-05 against the unified-context principle. **Verdict: the data layer conforms; the reasoning/skills/memory layer violates it.** Today it's "one shared database," not yet "one shared brain."

### ✅ Conforms
- **One profile.** Single `HERMES_HOME=/opt/data`, one `.hermes` dir. No per-account/per-channel profile split exists anywhere on disk. (`find / -name ".hermes*"` → only `/opt/data/.hermes`.)
- **One shared SQLite.** Every process — WhatsApp *and* email (`triage_agent.py:28`, `email_triage_agent.py:27`, both pollers/batchers, both MCP servers) — reads/writes the **same** `/opt/data/whatsapp-messages/whatsapp_data.db`. Triaged data is genuinely unified.

### ❌ Violations
| # | Violation | Evidence (live) | Principle broken |
|---|-----------|-----------------|------------------|
| 1 | **Two separate brains, not one.** WhatsApp & email are handled by two independent Python scripts, each assembling its own DeepSeek prompt. The real Hermes agent (which holds shared skills+memory) only *queries* the DB via MCP; it isn't the triage brain. | `triage_agent.py` / `email_triage_agent.py` each build their own prompt + one DeepSeek call | "handled by the same agent infrastructure" |
| 2 | **Skills are channel-siloed and asymmetric.** WhatsApp triage loads **only** `skills/whatsapp-triage/`; email triage loads `whatsapp-triage/` **+** `email-triage/`. Email sees WhatsApp rules, but WhatsApp can't see email rules. Neither uses Hermes native skill discovery. | `triage_agent.py:30` (`SKILLS_DIR` single dir); `email_triage_agent.py:29-30` (`WA_SKILLS_DIR` + `EMAIL_SKILLS_DIR`) | "same set of skills" |
| 3 | **Shared memory is a no-op → context not shared.** Both configs set `use_hermes_memory: true`, but neither script contains a single `memory`/`MEMORY` reference. `/opt/data/memories/` exists but the pipelines never read it. | `config.json` sets the flag; grep for memory in both triage scripts → **0 hits** | "same memory / context" |
| 4 | **Calendar entirely absent.** No calendar poller, no token, no calendar tables. 1 of 3 channels missing. | no `calendar*` files; no calendar tables in DB | "every calendar is just a channel, handled the same way" |

### How the plan fixes each
- **#1, #2** → one router + focus specialists running on the shared Hermes brain (native skill discovery), not per-channel scripts (§2, §5 P1–P2).
- **#3** → native `background_review` + the real shared memory store; turn the `use_hermes_memory` no-op into an actual read/write of `/opt/data/memories/` (§2, §4-learning, P4).
- **#4** → P0.5 calendar ingestion into the same DB + router (§1.5).

**Net:** today = *unified storage, siloed cognition*. The plan converts it to *one brain, all channels*.

---

## 2. Goal 1 — Skills-focus detection + per-focus self-improving processing

> **Decision (from you):** there is **no fixed focus list**. Focuses are **dynamically managed by you, exactly like skills** — you add, rename, retire, and edit them at will, and the router adapts automatically. Everything below treats the focus set as data, never as hard-coded enums.

### 2.0 Focuses are a dynamic registry (managed like skills)

A *focus* is itself just a Hermes skill bundle plus a registry row — so managing focuses reuses the same UX you already have for skills (`skill_manage`, `hermes skill …`, the dashboard/journey editor). No code change is needed to add a focus; you drop/author a `focus-<name>/SKILL.md` (or ask the agent to) and it appears.

- **`focuses` registry table** (SQLite): `name, description, enabled, autonomy_policy, created_at, source(user|agent), skill_dir`. This is the single source of truth the router reads at runtime.
- **Add a focus** = author `skills/focus-<name>/SKILL.md` + insert a registry row (one CLI/agent action, or the `/learn`-style flow). **Retire** = disable the row (soft, recoverable — same as the curator's archive/restore for skills). **Edit** = edit the skill bundle; hot-reloads on the next batch.
- The **router taxonomy is generated from the enabled registry rows every run** — add a focus and the very next message can route to it, with zero redeploy. This mirrors how the system-prompt skill index is rebuilt from the skills dir.
- The agent may *propose* a new focus when it keeps seeing messages that fit none of the current ones ("I'm seeing a recurring cluster about recruiting — create a `hiring` focus?") but **creating/enabling a focus is your call** via the intervention path (Goal 2).

### 2.1 Two-stage triage (router → focus specialist)

Replace the single classify call with **two stages**:

**Stage A — Focus router (cheap, deterministic-first).**
- Input: the batch (sender, subject/text, thread). Output: `focus` (one of the **currently-enabled registry focuses**, or `unknown`) + `confidence 0..1`. The candidate set is injected into the router prompt from the registry — never hard-coded.
- Fast paths before the LLM (mirroring the existing family/newsletter shortcuts in `process_batch_file`): learned sender→focus map (`focus_routes` table), keyword rules from each focus's `SKILL.md`. LLM only on ambiguity.
- Low confidence (`< threshold`), `unknown`, or "fits no focus but recurs" → **intervention** (Goal 2): ask you to pick a focus or create a new one; the answer is remembered (self-improve).

**Stage B — Focus specialist.**
- Each focus loads **its own skill bundle** and runs the extract/classify/escalate step with focus-specific rules and output schema (e.g. accounting extracts invoice#, amount, due date; business-leads extracts company, deal stage, next action).
- Two implementation options — **decision (from you): go step by step**, i.e. start with B2 and migrate hot paths to B1 later:
  - **(B2, first) Extend the existing standalone script** — add a `focus` dimension to `load_skills(focus=...)` so it loads `skills/focus-<name>/` on top of the shared base. Lowest lift, keeps the current working process model. This is the starting point.
  - **(B1, later) Hermes subagent** — dispatch via `delegate_task` with `toolset` narrowed to the focus's tools + inherited focus MCP (`inherit_mcp_toolsets=true`, already default). Gives isolated context, the real Hermes agent loop, and native skill access. Migrate to this once the taxonomy + skills stabilize.

### 2.2 Focus = a Hermes skill bundle (progressive disclosure)

Create one skill directory per focus under `HERMES_HOME/skills/`, following the native structure (`tools/skills_tool.py` header): `SKILL.md` (≤60-char description so it routes in the system-prompt index) + `references/`, `templates/`, and a `learned/` subdir (the new "custom/" home).

```
skills/
  focus-<name>/            SKILL.md, references/, learned/     # one per registered focus (dynamic; e.g. accounting, marketing, business-leads, personal — but YOU own the list)
  ...
  focus-router/            SKILL.md (routing meta-rules; candidate list comes from the registry, not this file)
```

Because focuses are dynamic, this directory grows/shrinks as you manage them — the diagram shows the shape, not a fixed set.

Registering them as real Hermes skills means the **main agent** (Telegram chat) can `skill_view` the same knowledge the triage uses — one source of truth, no divergence.

### 2.3 Self-improvement — use the native loop, not a new one

Hermes's self-improvement is: after a turn, `spawn_background_review` forks the agent with a **memory+skill-only toolset**, asks "should any skill/memory be saved/updated?", and writes via `skill_manage`. Skills it writes are tagged **agent-created** (`skill_provenance`), and the **curator** later consolidates/prunes *only those* (never your hand-written ones).

Wire the pipeline into this instead of the empty `custom/` dir:

- **Trigger on correction (highest signal).** When you correct a triage outcome via Telegram (Goal 2) — "this wasn't a lead, it's personal" / "always escalate invoices from Acme" — enqueue a background-review pass seeded with (original message, wrong output, your correction). It authors/edits a rule in the relevant `focus-*/learned/` skill via `skill_manage`, marked agent-created.
- **Periodic curator run.** Schedule `hermes curator` (via a cron job in `cron/jobs.json`) to dedup/prune the learned skills weekly so they don't bloat the prompt.
- **Because skills hot-reload**, the next batch for that focus immediately uses the new rule — same live-edit property you have today, but now driven by an actual learning loop with provenance and pruning.
- If staying on the standalone scripts (B2), implement a thin `focus_skill_improver.py` that mirrors this: it calls DeepSeek with the `_AUTHORING_STANDARDS` prompt from `learn_prompt.py`, writes to `focus-<name>/learned/<rule>.md`, and tags provenance in a sidecar so a `curator`-style pass can prune. (Reuses Hermes's exact authoring standards rather than inventing a format.)

**Guardrail:** never let the loop rewrite your hand-authored `SKILL.md` bodies — learned rules land only in `learned/` and are the only thing the pruner may touch (this is exactly the provenance rule in `skill_provenance.py`).

---

## 3. Goal 2 — Telegram human-in-the-loop intervention

Hermes already solved "ask the user and block the agent thread until they reply, over a gateway, with a timeout" — `tools/clarify_gateway.py`. It supports **inline-keyboard buttons** (Telegram `InlineKeyboardMarkup`), a **text fallback** ("reply with a number or free text"), a generated `clarify_id`, and `resolve_gateway_clarify(clarify_id, response)` from the button callback / text intercept.

### 3.1 Intervention triggers
- Router confidence `< threshold`, or focus = `unknown`.
- New sender/domain not mapped to any focus (ask which focus, or offer to **create a new focus** — Goal 1 — and remember the answer → self-improve).
- Escalation ambiguity ("looks urgent but I'm not sure").
- **Any outbound action is approval-gated by default.** **Decision (from you):** outgoing actions (send an email reply, create an invoice draft, add a calendar event, advance a lead stage) **request approval by default**; a specialist may act autonomously **only** where you have manually set an autonomy allowance. This is stored per-focus/per-action as `autonomy_policy` on the `focuses` registry row (and can be finer-grained per action type), enforced through `tools/approval.py`. Default = ask; you opt specific things into auto.

### 3.2 Mechanism
- **If Stage B uses subagents (B1):** the specialist just calls the native `clarify` tool; the gateway renders it on Telegram automatically. Zero new plumbing. (Note: `delegate_task` children are blocked from `clarify` by design — so the *router/orchestrator* runs in the main agent and asks; specialists return "need decision" up to it.)
- **If Stage B stays standalone (B2):** extend the existing `telegram_callback_handler.py` (it already handles inline buttons + text capture on port 7902) with a `send_clarify(question, choices)` that renders buttons and a `resolve` that writes the answer back to the batch's pending record, unblocking it. This is a faithful port of `clarify_gateway`'s two delivery paths.

### 3.3 Close the loop
Every intervention answer is a labeled training signal: feed it straight into the §2.3 self-improvement trigger so the same question isn't asked twice. Rate-limit and dedupe (the digest already models "don't spam"; reuse that).

---

## 4. Goal 3 — Per-focus MCP + self-improving MCP creation

> **Decision (from you):** focus MCPs are **SQLite-backed first**, and can grow to external sources later. And the standout requirement: the agent should be able to **clone a public open-source GitHub project and turn it into an internal, locally-running solution on the hermes-agent instance, using the local SQLite as its data source** — see §4.3.

### 4.1 One MCP server per focus (dynamic, SQLite-first)
You already have the pattern: `mcp_server.py` (WhatsApp, :8650) and `email_mcp_server.py` expose triaged data as MCP tools the main agent calls. Generalize it so **each registered focus can own an MCP server** (created on demand, not a fixed set):

- e.g. an accounting focus → `list_open_invoices`, `mark_paid`, `aging_report`, `create_invoice_draft`; a leads focus → `list_leads`, `advance_stage`, `next_actions`, `log_touch`. Which focuses exist, and thus which MCPs exist, is driven by the registry (§2.0).
- **Phase 1 backing = the shared SQLite only** (focus-scoped views). External APIs come later, per focus, as a follow-on.
- Read tools are freely callable; **write/action tools honor the approval-by-default policy** (§3.1) — the MCP tool checks `autonomy_policy` and raises an approval request unless you've allowed it.

Each is a small `fastmcp` stdio (or SSE) server. **Register each with the native CLI** so the main agent and subagents pick them up:

```
hermes mcp add focus-<name> --command python3 --args /opt/data/mcp/focus_<name>_mcp.py
```

Config lands in `config.yaml` under `mcp_servers` (`hermes_cli/mcp_config.py`). Because `delegation.inherit_mcp_toolsets` defaults **true** (`delegate_tool.py:525`), a focus subagent automatically gets its focus MCP tools — the specialist can *act*, not just classify.

### 4.2 Self-improving MCP creation (the ambitious part)

> **What exists vs. what's net-new (important):** Hermes has **no autonomous MCP-creation code today** — no gap-detector, no self-triggered generate-and-register loop. What it ships are *building blocks* driven by a human ask: the **`fastmcp` skill** (`optional-skills/mcp/fastmcp/SKILL.md`, teaches the agent *how to* author a server with its terminal/file tools), the `native-mcp`/`mcporter` skills (connect/CLI-access an existing server), `hermes mcp add|install|catalog|serve`, a human-curated catalog (`optional-mcps/<name>/manifest.yaml`, pinned, never auto-updated), and `validate_mcp_server_entry` (`mcp_security.py`, a gate that runs on `add`). So the closest thing today is "*you* ask → agent follows the `fastmcp` skill → `hermes mcp add`" = agent-assisted authoring, not a factory. **Net-new in this plan:** the gap-detection signal, the auto-authoring trigger, and the two-step Telegram approval gate. These are built *on top of* the existing primitives (fastmcp skill for authoring, `validate_mcp_server_entry` for safety, `hermes mcp add` for registration), and they ride the MCP rung of the footprint ladder — never a new core tool (AGENTS.md).

Model it on the existing **MCP catalog** (`hermes_cli/mcp_catalog.py`): catalog entries are `optional-mcps/<name>/manifest.yaml`, installed via `hermes mcp install`. Add an **"MCP factory"** capability that lets the system grow a *new* tool/server when a focus repeatedly hits a capability gap:

1. **Detect the gap.** During specialist runs / interventions, log "wanted-but-missing" actions (e.g. repeatedly asked to "reconcile against the bank feed"). A threshold-based signal (like the curator's consolidation trigger) proposes a new capability.
2. **Author it.** Reuse the `/learn`-style authoring flow (`learn_prompt.py` pattern) but targeting an MCP: a `mcp-authoring` skill + a fastmcp template under `templates/`. The agent writes `focus_<name>_mcp_v2.py` (or a new server) with the new tool, plus a manifest.
3. **Gate it.** New MCP servers = new executable surface → **decision (from you): building AND applying a new MCP always requires your explicit Telegram approval** before registration. Two-step gate: (a) approve *starting the build*, (b) approve *applying/registering* the built server. This mirrors the catalog's "presence = approval" policy and the `mcp_security.validate_mcp_server_entry` check that already runs on add.
4. **Register + hot-add.** On your approval, run `hermes mcp add …`; `mcp_startup.py` brings it into the toolset. Provenance-tag agent-created MCPs so a curator pass can retire unused ones.

**Safety rails (from AGENTS.md + `mcp_security.py`):** secrets only in `.env`; validate every entry; never auto-register without approval; keep new capability at the MCP rung (not new *core* tools, which ship on every API call and break the cache bar).

### 4.3 Open-source → internal solution pipeline (clone GitHub → run locally → SQLite-backed → expose as MCP)

This is the most powerful and the highest-risk capability, so it is **fully approval-gated and staged**. Goal: when a focus needs a capability that a mature open-source project already provides (e.g. an invoicing engine, a CRM, a rules engine), the agent can **turn that project into an internal tool running on the hermes-agent instance, wired to the local SQLite**, then expose it as a focus MCP — instead of hand-writing everything.

**Pipeline stages (each an approval checkpoint over Telegram):**
1. **Propose.** Agent identifies a candidate public repo for a stated need and presents: repo URL, license, stars/activity, what it would be used for, and how it maps to the focus. → *You approve evaluating it.*
2. **Vet.** License check (permissive/allowed only), dependency + supply-chain scan, secret scan, and a sandbox smoke test. No network egress beyond what's declared. Summarized back to you.
3. **Adapt.** Clone into `/opt/data/internal-solutions/<name>/`, add a **SQLite data adapter** so the project reads/writes the local DB (via focus-scoped views) instead of its own store, and strip/disable anything not needed. All changes tracked in a local git repo for auditability.
4. **Run locally.** Launch as an isolated local service (its own venv/container-in-container or process, non-privileged, bound to localhost) — never exposed publicly. Health-checked like the existing pipeline services.
5. **Expose as MCP.** Wrap the service behind a thin `fastmcp` server (`focus_<name>_solution_mcp.py`) so it becomes normal focus tools; register via `hermes mcp add` (the §4.2 two-step build+apply approval applies).
6. **Maintain / retire.** Provenance-tagged; pinned to a commit (no silent upstream updates — mirrors the catalog's "never auto-update" rule); a curator pass flags unused solutions for retirement.

**Hard rails specific to this pipeline:** license allowlist enforced; run non-root, localhost-only, no outbound network unless you approve it; pin the exact commit; everything in a dedicated `internal-solutions/` tree under git; **two human approvals minimum** (evaluate, then apply). This is the riskiest surface in the whole design — treat every step as opt-in, never autonomous.

---

## 5. Phased rollout (each phase independently shippable + testable)

| Phase | Deliverable | Approach |
|-------|-------------|----------|
| **P1 — Focus taxonomy + router** | `focus-router/SKILL.md`, `focus_router` stage added before existing triage; adds `focus`+`confidence` columns to DB; logs routing decisions (no behavior change yet) | Extend `triage_agent.py`/`email_triage_agent.py` (B2) |
| **P2 — Focus specialists + skill bundles** | 4 `focus-*` skill dirs; specialist step loads focus bundle; per-focus output schemas | B2 (`load_skills(focus=)`) first; migrate hot paths to `delegate_task` (B1) later |
| **P3 — Telegram intervention** | low-confidence/new-sender/approval questions over Telegram with buttons; blocking+timeout; dedupe | Port `clarify_gateway` semantics into `telegram_callback_handler.py` (or native `clarify` if B1) |
| **P4 — Self-improvement loop** | corrections + intervention answers author/edit `learned/` rules; weekly curator prune; provenance | Native `background_review` + `curator` (B1) or `focus_skill_improver.py` (B2) |
| **P5 — Per-focus MCP (SQLite-backed)** | one MCP server per focus, registered via `hermes mcp add`; write tools approval-gated; specialists can act | Generalize `mcp_server.py`; `inherit_mcp_toolsets` |
| **P6 — MCP factory (self-improving infra)** | gap detection → author new MCP tool → **two-step** Telegram approval (build, apply) → register | Catalog/manifest pattern + `/learn`-style authoring + approval gate |
| **P7 — OSS→internal-solution pipeline** | clone/vet/adapt a public repo → SQLite adapter → run locally → expose as focus MCP; multi-approval | §4.3; strictly last, highest-risk, fully gated |
| **P0 — Focus registry + dynamic router taxonomy** | `focuses` registry table + manage-like-skills UX; router reads registry | Precedes P1; makes the focus set dynamic |
| **P0.5 — Calendar ingestion (3 accounts, one profile)** | `calendar_poller.py`/`calendar_batcher.py`; per-account tokens under `/opt/data/calendar-messages/tokens/`; `calendar_events` tables in the shared DB | §1.5; unifies the 3rd channel into the single context |

**Decision (from you): go step by step.** Sequence: **P0 → P0.5 → P1 → … → P7**, B2 first, migrating hot paths to **B1 subagents** around P4+ once the taxonomy and skills stabilize — that's when native self-improvement + `clarify` + `inherit_mcp_toolsets` pay off most. P7 (OSS→internal) is deliberately last.

---

## 6. Data-model additions (SQLite)
- `messages`/`email_messages`: add `focus TEXT`, `focus_confidence REAL`.
- New `focus_routes` table: `handle/domain → focus` (learned sender map; populated by interventions).
- New `interventions` table: `id, batch_id, question, choices, status(pending/answered/timeout), answer, created_at, answered_at` (drives the blocking queue + becomes training data).
- New `learned_rules` provenance sidecar: `skill, rule_file, origin(agent/user), created_at, last_used, uses` (feeds the curator/pruner).

## 7. Risks / constraints (from the codebase's own rules)
- **Prompt-cache safety:** the main Telegram agent's system prompt must stay byte-stable within a conversation (AGENTS.md "caching is sacred"). Focus skills are fine (progressive disclosure, loaded on demand); do **not** hot-swap the main agent's toolset mid-conversation.
- **Footprint ladder:** no new *core* model tools — everything here is CLI + skill + MCP. New capability arrives as MCP servers, not core surface.
- **`.env` = secrets only.** All thresholds/focus config live in `config.json`/`config.yaml`, not new `HERMES_*` env vars.
- **Subagent limits:** `delegate_task` children can't `clarify`/`memory`/`cronjob`/recurse (`DELEGATE_BLOCKED_TOOLS`) — so the orchestrator (main agent) owns intervention + learning writes; specialists return structured asks.
- **MCP creation is privileged:** always approval-gated + `validate_mcp_server_entry`; provenance-tag + curate to avoid accumulating dead servers.

## 8. Decisions locked in (from your answers)
0. **Unified single context — one profile for everything.** All channels (2 WhatsApp + 3 email + 3 calendar) run in one `HERMES_HOME=/opt/data`, sharing skills, memory, focuses, and learned rules. Multiple Google accounts are handled with per-account token files, NOT separate profiles (§0.5, §1.5).
1. **Focus list — dynamic, no fixed set.** Focuses are managed by you like skills: add/rename/retire/edit at will; the router reads the live registry (§2.0). The agent may *propose* new focuses but only you create/enable them.
2. **Action authority — approval by default.** Every outbound action requests approval unless you have manually granted autonomy for that focus/action (`autonomy_policy`, §3.1).
3. **Rollout — step by step.** P0→P7, standalone (B2) first, migrate hot paths to native subagents (B1) later (§5).
4. **MCP factory — approval required to build AND apply.** Two-step Telegram gate before any new MCP is created and registered (§4.2).
5. **MCP data sources — SQLite first, external later; plus OSS→internal pipeline.** Focus MCPs start SQLite-backed; can connect external sources later. New capability: the agent can clone public open-source GitHub projects and turn them into internal, locally-running, SQLite-backed solutions exposed as focus MCPs — fully approval-gated and staged (§4.3, P7).

## 9. Remaining questions before implementation
1. **Approval UX granularity** — is a per-focus autonomy toggle enough, or do you want per-action-type control (e.g. "auto-draft replies but always approve sends")?
2. **Intervention timeout behavior** — if you don't answer a Telegram question within N minutes, should the item hold (stay pending), fall back to "escalate to digest", or take a safe default? 
3. **OSS pipeline license policy** — which licenses are allowed for internal adaptation (e.g. MIT/BSD/Apache-2.0 yes; GPL/AGPL ask-first)? 
4. **Calendar account type** — are the 3 Google accounts personal Gmail or Workspace-managed? Determines OAuth approach A vs. B and the consent-screen/test-user setup (§1.5.1).
5. **First focus to build end-to-end** — which single focus should P1–P5 target first as the reference implementation (e.g. business-leads or accounting)?
6. **Where should this plan live** — commit it into `leolau/hermes-agent` (e.g. `docs/`), keep it as the on-instance `IMPLEMENTATION.md` companion, or both? **(Decided: both, once planning is finished.)**

---

## Appendix A — How the Hermes built-ins actually work (source-grounded)

This appendix documents the existing primitives the plan wires into, so the design isn't a black box. All references are to `leolau/hermes-agent` @ `10499e75a`.

### A.1 `skill_manage` — skill CRUD (`tools/skill_manager_tool.py`)
The agent's tool for turning knowledge into reusable skills. Actions: `create` (new `SKILL.md` + dir), `edit` (full rewrite), `patch` (targeted change), `write_file` (add a `references/`/`templates/`/`scripts/` file), `delete`.
- Skills live at `HERMES_HOME/skills/<name>/SKILL.md` with YAML frontmatter (`name`, ≤60-char `description`, tags). The **description is loaded into every system prompt** as the skill index (progressive disclosure); the full body is pulled on demand via `skill_view`.
- **Provenance-aware:** records whether a write came from a foreground (user-directed) agent or the background-review fork (`tools/skill_provenance.py`). Only *agent-created* skills are curation-eligible; hand-written ones are never auto-touched.
- **Guards:** pinned skills can't be auto-archived; optional security scan for agent-created skills (`skills.guard_agent_created`); background-review writes must read-before-write.

### A.2 `background_review` — the self-improvement fork (`agent/background_review.py`)
After a turn, `run_agent.py:_spawn_background_review` may fire a **daemon-thread copy** of the agent that replays the conversation snapshot and asks *"should any skill/memory be saved/updated?"*.
- Runs with a **whitelisted toolset = memory + skill tools only**; everything else denied at runtime. Writes tagged `background_review` origin.
- **Cache-safe:** same model → reuses the parent's warm prompt-cache prefix (cheap); different aux model → replays a compact digest to minimize cold tokens. Never mutates the main conversation or its cache.

### A.3 `curator` — the skill janitor (`agent/curator.py`, `hermes_cli/curator.py`)
A periodic, **inactivity-triggered** maintenance pass (no cron daemon): when the agent is idle and the last run was >`interval_hours` ago (default **7 days**), it forks an **auxiliary-model** agent to tidy the collection.
- **Invariants:** only touches agent-created skills; **never deletes — only archives** (recoverable via `hermes curator restore`); pinned skills exempt; uses the aux client so it **never touches the main prompt cache**.
- Auto-transitions lifecycle by activity age (active→stale after ~30d→archived after ~90d); optional LLM "consolidation" clustering is **off by default**. CLI: `hermes curator status|run|pause|resume|pin|unpin`.

### A.4 `delegate_task` — subagents (`tools/delegate_tool.py`)
Spawns **child AIAgent instances** with isolated context (single or parallel batch); parent blocks until they finish and sees only the summary.
- Each child: fresh conversation, own terminal/`task_id`, restricted toolset.
- **`DELEGATE_BLOCKED_TOOLS`** = `delegate_task` (no recursion), `clarify`, `memory`, `send_message`, `cronjob`, `execute_code` — so children can't ask the user or write shared memory; the orchestrator (main agent) owns intervention + learning.
- **`delegation.inherit_mcp_toolsets` defaults true** (`delegate_tool.py:525`) → a child auto-gets the parent's MCP tools. Config: `max_concurrent_children` (3), `max_spawn_depth` (1).

### A.5 `clarify_gateway` — ask-and-block over Telegram (`tools/clarify_gateway.py`)
The primitive behind the `clarify` tool in gateway mode. Stores a pending request with a `clarify_id`, **blocks the agent thread on an Event with a timeout**, resolves when the reply arrives via `resolve_gateway_clarify(clarify_id, response)`.
- **Buttons path:** Telegram `InlineKeyboardMarkup` with a final "Other (type answer)" → free-text capture.
- **Text fallback:** numbered list; user replies with a number or free text, intercepted in `_handle_message`.
- Timeout guarantees an unanswered question won't hang the agent. `tools/approval.py` gives the same shape for *action approvals*.

### A.6 `hermes mcp` — MCP lifecycle (`hermes_cli/mcp_config.py`, `mcp_catalog.py`, `mcp_startup.py`)
CLI: `add / remove / list / test / configure / install / picker / catalog / serve`. See Appendix B for a full worked example.
- `add` is **discovery-first**: it probes the server for its tools, runs `validate_mcp_server_entry` (`mcp_security.py`), then persists to `config.yaml` under `mcp_servers` (stdio `command`+`args`+`env`, or `url` + OAuth/header).
- At process start, `mcp_startup.start_background_mcp_discovery` reads `mcp_servers` and connects **in a background thread** (never blocks startup); `mcp_tool.py` registers each server's tools into the tool registry (re-registering live on reconnect).
- **Catalog** = curated `optional-mcps/<name>/manifest.yaml` (presence = Nous approval), installed via `hermes mcp install`; pinned, never auto-updated; secrets go to `.env`. `hermes mcp serve` exposes Hermes *itself* as an MCP server.

### A.7 Other Hermes concepts that affect this design

**Directly relevant to the build:**
- **Gateway + Channels** (`gateway/run.py`, `gateway/platforms/*`). The gateway is the long-lived daemon connecting the one agent to ~20 platforms; a *channel* is one inbound platform adapter. Native version of "incoming channels" — Hermes ships an `email` channel and Telegram, so the intervention path (Goal 2) rides the gateway rather than bespoke Telegram code.
- **Cron / scheduled jobs** (`tools/cronjob_tools.py`, `cron/`). Durable scheduled turns (persisted jobs), unlike ephemeral `delegate_task`. Pollers/digests map here natively; one live cron already exists ("DeepSeek balance alert").
- **Heartbeat** (`HEARTBEAT.md`). Periodic self-prompt for background work on a timer (e.g. "check inbox + calendar, act if needed"). Currently empty/unused on this box — the native hook for proactive triage cycles.
- **Approval gates** (`tools/approval.py`, `tools/write_approval.py`). A full, existing approval framework (~300 refs) — the real backbone for "approval-by-default" (Goal 2), broader than `clarify` alone.
- **Context compression / compaction** (`trajectory_compressor.py`). Summarizes older turns to fit the model window — the one sanctioned exception to prompt-cache stability; creates `parent_session_id` chains in SessionDB. Long-running triage sessions will hit this.
- **Plugins** (`plugins/…` + Footprint Ladder). Sanctioned way to add capability without touching core (memory/model providers, notifiers, kanban). The OSS-integration ambition (§4.3) is essentially "author a plugin/MCP." Memory-backend plugins already exist (honcho, mem0, supermemory) for richer-than-`MEMORY.md` memory.

**Security & safety:**
- **Tirith** (`tools/tirith_security.py`, ~100 refs). Security scanner that inspects shell commands for dangerous patterns before the terminal tool runs them — a real gate on autonomous action.
- **Threat-pattern scanning** (`tools/threat_patterns.py`). Shared prompt-injection/exfiltration detection applied to memory, context files, and tool results.
- **YOLO mode.** The toggle that bypasses manual tool-approval — the off-switch this design deliberately does NOT flip globally (approval-by-default is the opposite stance).

**Architecture / cost:**
- **Profiles** (`hermes_constants.get_hermes_home()`). The isolation boundary behind skills/memory/sessions — what the "one profile" principle stands on. Profiles are intentionally independent islands (the repo rejects PRs that couple them).
- **Prompt caching (3-tier assembly).** The sacred constraint: the system prompt stays byte-stable within a conversation so the cached prefix is reused each turn. Why memory/skills use frozen snapshots; mid-conversation prompt mutation multiplies cost.
- **MoA (Mixture-of-Agents).** Optional multi-model advisor/aggregator; explains "aux model"/advisor plumbing.
- **Terminal backends** (`tools/environments/{local,docker,ssh,modal,daytona}.py`). The terminal tool can run locally or in Docker/SSH/sandboxes — relevant to the OSS-clone-and-run-locally idea (§4.3); sandboxed execution is a native concern.
- **Slash commands** (`/learn`, `/memory`, …). Shared command surface across CLI and gateway; `/learn` is the authoring flow the plan reuses.
- **`send_message` + `session_search` tools.** Proactive outbound messaging, and full-text search over the transcript store.
- **Managed mode / Credential pool.** Subscription access + rotating API keys for provider failover (mostly ops).

### A.8 `memory` — persistent curated memory (`tools/memory_tool.py`) — *how it works, as originally built*
Two bounded, file-backed stores that persist across sessions: **`MEMORY.md`** (the agent's own notes/observations) and **`USER.md`** (what the agent knows about the user). Distinct from `SessionDB` (the SQLite transcript store with FTS5 search).
- **Storage:** profile-scoped at `HERMES_HOME/memories/` (`get_memory_dir()`, `memory_tool.py:55-57`) → `/opt/data/memories/{MEMORY,USER}.md` on this box. Per-profile, same isolation as skills.
- **Read timing:** loaded **once at session start** as a **frozen snapshot** injected into the system prompt (`load_from_disk`, `memory_tool.py:168-205`). Mid-session writes hit disk immediately but do **not** change the current prompt (prefix-cache safety); new entries enter the prompt only on the **next** session start. Entries are threat-scanned at snapshot build; a poisoned entry becomes a `[BLOCKED: …]` placeholder in the prompt but stays in the file.
- **Write path:** the `memory` tool (`action = add | replace | remove`, unique-substring matching, char-bounded). `background_review` is a key writer (turns "should this be remembered?" into memory entries).
- **Access scope — NOT all agents.** Main agent (per profile) reads the snapshot and can read/write. **Subagents cannot:** `memory` is in `DELEGATE_BLOCKED_TOOLS` (`delegate_tool.py:49`, no writes) **and** children are built with `skip_memory=True` (`delegate_tool.py:1321`, snapshot not injected → no reads). Subagents are intentionally memory-less; the orchestrator owns memory and must pass any needed context into a child's task briefing.

---

## Appendix B — `hermes mcp` worked example

Goal: register a small MCP server that exposes the WhatsApp/email SQLite data as tools the agent can call. (This is exactly how the plan's per-focus MCPs get added.)

**1. The MCP server (stdio, `fastmcp`)** — `/opt/data/mcp/focus_leads_mcp.py`:
```python
from fastmcp import FastMCP
import sqlite3

mcp = FastMCP("focus-leads")
DB = "/opt/data/whatsapp-messages/whatsapp_data.db"

@mcp.tool()
def list_leads(status: str = "open", limit: int = 20) -> list[dict]:
    """List business leads by status."""
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, company, stage, next_action, updated_at "
        "FROM leads WHERE status=? ORDER BY updated_at DESC LIMIT ?",
        (status, limit),
    ).fetchall()
    return [dict(r) for r in rows]

if __name__ == "__main__":
    mcp.run()   # stdio transport by default
```

**2. Register it (discovery-first, validated, saved to `config.yaml`):**
```
hermes mcp add focus-leads --command python3 --args /opt/data/mcp/focus_leads_mcp.py
```
For a server that needs secrets, pass `--env KEY=VALUE` (stdio only; values land in `.env`). For a remote HTTP/SSE server you'd use `--url https://… [--auth oauth|header]` instead.

**3. What lands in `config.yaml` under `mcp_servers`:**
```yaml
mcp_servers:
  focus-leads:
    command: python3
    args: [/opt/data/mcp/focus_leads_mcp.py]
  # (existing entries, for reference — the current live config already has:)
  whatsapp:
    type: http
    url: http://localhost:8650/jsonrpc
  cost-tracker:
    command: /opt/data/.venv/bin/python3
    args: [-u, /opt/data/mcp_cost_tracker.py]
```

**4. Verify + use:**
```
hermes mcp list          # shows focus-leads
hermes mcp test focus-leads   # probes the server, lists discovered tools
```
On the next agent start, `mcp_startup` connects `focus-leads` in the background and registers `list_leads` into the tool registry. The main Telegram agent (and any subagent, since `inherit_mcp_toolsets` is true) can now call it — e.g. you ask "show me my open leads" and the agent invokes the `list_leads` MCP tool and answers. Write/action tools (e.g. `advance_stage`) would additionally run through the approval gate (§3.1) unless you've granted autonomy.

**How this maps to the plan:** each focus gets one such server (SQLite-first), registered the same way; the MCP factory (§4.2) authors new servers from a template and runs this same `hermes mcp add` step behind a two-step approval; the OSS→internal pipeline (§4.3) wraps a cloned project behind the same kind of `fastmcp` shim and registers it identically.
