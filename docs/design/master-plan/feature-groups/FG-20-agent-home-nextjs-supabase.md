# FG-20 — `agent-home`: mobile-first Next.js face on the Python AI layer + Supabase

**Wave:** Phase-3 (A→B→C) · **Owner agent:** _unassigned_ · **Status:** PLAN — **owner-confirmed** (Leo, 2026-07-11: all four decisions in §"Open decisions (RESOLVED)" locked); ready to implement (Wave A first). No code yet.

## Summary
Build a **new, mobile-first web app called `agent-home`** that becomes the
**user-facing "face"** of the system and hosts **all the Phase-2 features** in a
UI that is actually usable on a phone. The current `web/` dashboard is a
desktop-oriented power-user/operator console; it is **hard to use and not
mobile-friendly**, so rather than keep bolting user features onto it, we stand
up a purpose-built app.

Architecture is fixed (Leo, this session) as **three tiers**:

```
Next.js (agent-home, mobile-first UI)
   → Python (the AI / agent layer + API: one-brain gateway, agent tools,
             CDP webview, Core write-guard, promotions, readiness, C1/C2/C6/C8)
      → Supabase (Postgres + Storage + RLS — the shared datastore)
```

`agent-home` **reuses**, it does not re-implement: the agent brain, the access
model (C1/C2), consent (C6), and tracing (C8) all stay in the Python AI layer;
Supabase stays the datastore. `agent-home` is a **client of both** — it calls
the Python API for anything agent- or authority-related, and uses Supabase
(server-side + Realtime) for read-heavy data views. It does **not** grow the
core agent, add a second brain, or fork the datastore (per `AGENTS.md`
extend-don't-duplicate).

**The existing `web/` dashboard is retained** as the operator/admin console
(Sessions, Models, Cron, Plugins, Env, Files, Logs, MCP, Profiles, Channels,
Pairing, Config — the Hermes-native surfaces). `agent-home` owns the **user
face + the Phase-2 features**; overlapping user panels can be retired from
`web/` later once `agent-home` reaches parity.

## Decisions applied
- **D16 — `agent-home` is a separate, mobile-first Next.js App-Router app; the system is a fixed three-tier stack: Next.js UI + Python AI layer (API) + Supabase (storage/DB).** The browser never receives a service-role key and never bypasses C1/C2/C6/C8 — authority-sensitive access goes through the Python API; Supabase is reached server-side (RSC / route handlers) and via RLS-scoped Realtime for live views. `agent-home` code is **Core (C7)** — runtime agent can't edit it; only human devs via PR.
- Reaffirms **D3** (in-house/user-facing web = Next.js + Node), **D12/D13** (Next.js face + one-brain chat surface), **D1/D5** (multi-user, dev→prod), **D10** (Core immutable).
- Consumes contracts **C1** (principal), **C2** (visibility + RLS), **C3** (dev/prod datastore), **C5** (change log), **C6** (consent), **C7** (Core boundary), **C8** (interaction trace), **C9** (GTS graph + assignment). **No new contract** — it is a new *surface* over existing contracts.

## Reuse map
- **Python API layer** (`hermes_cli/web_server.py` `/api/*`) — the AI/agent + authority API. `agent-home` calls it for: one-brain chat, agent webview (CDP), GTS authority writes (assign / eval-method / promote), onboarding readiness, Core manifest/health, tool enable/promote/health, comms actions, change undo/redo. **No backend rewrite.**
- **Supabase** (`app_dev`/`app_prod` Postgres via C3; Storage) — direct **server-side** reads + **Realtime** for GTS graph, interaction traces, change log, tool registry listings, notifications; Storage for chat attachments/media.
- **Access model** — `hermes_cli/access.py` (C1/C2 + FORCE'd RLS via `hermes.principal_id`/`hermes.principal_role` GUCs), `interactions.py` (C8 RLS), `gts.py`/`item_grants` (C9 + FG-19 grants). Reused as-is through the API and (server-side) through an authenticated Supabase context.
- **Existing UI** — `web/src/screens/*` (`GtsCentrePage`, `CorePage`, `OnboardingPage`, `TelegramPage`, `WebviewPage`, `ToolsPage`, `CommsPage`, trace views) are the **functional reference** for the ported features; Nous-UI theme tokens can be reused, but layouts are **redesigned mobile-first** (the whole point).
- **`data-component` babel plugin** (`web/tools/babel-plugin-data-component.cjs`) — reused so deployed `agent-home` is DevTools-inspectable.
- **`dashboard_auth/`** — bridged into the `agent-home` session (see §Auth).

## Design / approach
1. **New app, mobile-first.** `agent-home/` (sibling of `web/`): Next.js 15 App
   Router + TypeScript + Tailwind, a **mobile-first component system**
   (bottom-nav, large touch targets, responsive/stacked layouts, sheet/drawer
   patterns), installable **PWA** (app icon, offline shell), `supabase-js`
   (server + realtime). Fresh layout; reuse theme tokens/`data-component` std.
2. **BFF pattern.** `agent-home` server (RSC + route handlers) is a thin
   backend-for-frontend: it holds the authenticated principal context, proxies
   agent/authority calls to the **Python API**, and does **server-side** Supabase
   reads with the principal's RLS context. The **browser** only ever talks to
   the `agent-home` server + RLS-scoped Realtime — never raw Supabase with a
   privileged key.
3. **Feature port matrix** (all Phase-2 features move in):

   | Feature (Phase-2 source) | Reads | Writes / agent / authority |
   |---|---|---|
   | **GTS Centre** — goals/tasks/skills graph, scores, assignment + watchers (FG-18/19) | Supabase (server + Realtime), RLS-scoped | Python API `/api/gts/*` (assign/reassign/accept/decline, eval-method, promote) — C2/C6 enforced |
   | **Onboarding** wizard + readiness (FG-15) | — | Python API `/api/onboarding/readiness` (needs on-box env/secret presence signals) |
   | **Core-area view** (FG-14) — manifest/health + change log + trace | change log/trace: Supabase (RLS) | manifest/health: Python API `/api/core/manifest` (file-based on box) |
   | **Interaction trace** timeline (FG-16/C8) | Supabase (RLS) + Realtime | — (read-only observability) |
   | **Agent chat** — one-brain, Telegram-equivalent pane (FG-03/17/D13) | conversation history via API/Supabase | Python API (one-brain gateway) + Supabase **Storage** for attachments/media |
   | **Agent webview** — CDP, consent-gated (FG-17b/C6) | live view via API | Python API `/api/webview/*` (CDP + C6 consent + C8 trace) — **not** Supabase-native |
   | **Tools** registry — list/enable/promote/health (FG-07) | Supabase (list) | Python API `/api/tools/*` (enable/config/promote dev→prod/health) |
   | **Comms / notifications**, change undo/redo (FG-10/12) | Supabase (lists) | Python API `/api/comms/*` |

4. **Auth (see Open decisions).** Bridge the existing C1 principal / `dashboard_auth`
   login into an `agent-home` session cookie; the `agent-home` server establishes
   the principal's RLS context for its Supabase reads (set the same
   `hermes.principal_*` GUCs on its server-side connection, mirroring the Python
   backend). Owner sees all; members see own/shared/granted — **identical C2
   semantics, enforced in Postgres**, not re-implemented in JS.
5. **Cache-, alternation-, trace-safety preserved.** Chat still routes to the
   one-brain gateway unchanged (no prompt mutation, strict alternation); traces
   are read-only side-channel (never injected). `agent-home` adds **zero** new
   model tools and **zero** new `HERMES_*` non-secret env vars (behavioural
   config stays in `config.yaml`).
6. **Deploy (see Open decisions).** Default: served **on the prod box behind
   Caddy** (new route/subdomain, same Supabase), matching the current prod
   topology; Vercel is a later option (requires Supabase/Kong publicly
   reachable + the GoTrue bridge).

## Data model
- **No new backend schema.** Reuses `app_*` tables (C3), `item_grants` (C9/FG-19),
  `interactions` (C8), `changes` (C5), tool registry, plus Supabase **Storage**
  buckets for chat attachments (access-scoped by C2, one bucket path per user).

## Dev/Prod + Supabase
- `agent-home` reads/writes through C3 (`app_dev` in dev, `app_prod` in prod);
  it never bypasses mode routing. Dashboard/CLI stay `datastore.mode=dev`;
  channel traffic stays prod-routed (unchanged from the cutover).

## Testing requirements
- **Parity checklist:** every Phase-2 feature reachable + functional in
  `agent-home` vs the current `web/` panel it replaces.
- **Mobile:** responsive/touch checks at phone widths (bottom-nav, sheets,
  no horizontal scroll); PWA installability + offline shell.
- **Negative-access (required):** through `agent-home`, a member sees **only**
  own/shared/granted GTS + only own traces; owner sees all — verified against
  **real Postgres RLS**, not app-layer filtering. Chat/Storage media are
  C2-scoped (a user can't fetch another user's attachment).
- **Cache-safety:** prove the chat path leaves prompt bytes identical (one-brain
  unchanged); traces never enter the prompt.
- **Consent (C6):** webview + agent-initiated GTS assignment still gate + audit.
- Web lint/typecheck/build green; `ruff`/`ty` for any Python API additions;
  baseline green.

## System testing (system-test box)
**Required after this FG's development completes** (part of DoD), on the ECS
(`hermes-systest`, 4/16, EIP `47.83.199.25`) against **staging** (`app_dev`)
(**never prod**). See README §7.1. Checklist:
- `agent-home` deployed behind Caddy on the box; login bridges the C1 principal;
  all Phase-2 features exercised end-to-end on a **phone-width** viewport.
- ≥2 real test users: member sees only own/shared/granted GTS + own traces
  (real RLS); owner sees all; a member cannot fetch another's chat attachment.
- One-brain chat round-trips from `agent-home` and interleaves with Telegram
  (same session/brain); webview action gates on C6 + is traced (C8).
- **Gate:** not complete/promotable until this checklist passes (owner-gated).

## Dependencies
- **Blocked by:** all Phase-2 FGs (14–19) — merged. Reuses their API/data.
- **Blocks:** nothing (it is a leaf surface).
- **New contract:** none (new surface over C1/C2/C3/C5/C6/C7/C8/C9).

## Definition of Done
Parity checklist green; mobile/PWA checks pass; negative-access RLS + C6 +
cache-safety tests green; baseline + web build + `ruff`/`ty` green;
`agent-home` deployed behind Caddy on the box and reachable on a phone; **ECS
system test green** (owner-gated).

## Open decisions (RESOLVED — owner-confirmed, Leo 2026-07-11)
1. **Auth/identity for `agent-home`→Supabase → BRIDGE C1 principal → server-side
   Supabase context.** Set the same `hermes.principal_*` GUCs on `agent-home`'s
   server DB connection; **zero RLS rework**, reuses today's C2 model exactly;
   the browser never touches raw Supabase. (GoTrue/`auth.uid()` browser-direct
   is explicitly **deferred** to a possible follow-up, not this build.)
2. **Deploy target → ON-BOX behind Caddy** (new route/subdomain on the prod box,
   same Supabase), matching the current prod topology. (Vercel deferred.)
3. **Fate of `web/` → COEXIST (option A).** `agent-home` = the user/mobile face;
   `web/` stays the **operator/admin console** (Sessions, Models, Cron, Plugins,
   Env, Files, Logs, MCP, Profiles, Channels, Pairing, Config, Webhooks,
   System). Both hit the same Python API + Supabase. During transition the
   Phase-2 user panels exist in both; once `agent-home` reaches parity, **retire
   only those duplicated user panels from `web/`**, leaving the admin surfaces.
   Full replacement is a possible later step, not this build.
4. **App location → `agent-home/` at repo root** (sibling of `web/`).

## Progress checklist
- [x] Owner confirms Open-decisions §1–4 (Leo, 2026-07-11: bridge-C1 auth, on-box Caddy, coexist `web/`, `agent-home/` at root)
- [x] Wave A1: `agent-home` Next.js skeleton (App Router, Tailwind, mobile shell + bottom-nav, PWA, `supabase-js`, `data-component`, build/CI, on-box Caddy route)
- [x] Wave A2: auth + data-access foundation (C1 principal bridge → server-side Supabase RLS context; typed Python-API client; shared types) — **published as a small interface PR first**
- [x] Wave B1: GTS Centre (graph, scores, assignment + watchers) — read-only mobile view over `/api/gts/graph` (assignment writes deferred: no HTTP write API exists — creation/scoring/assignment stay on the CLI/agent authority paths)
- [ ] Wave B2: Core-area view + interaction-trace timeline
- [ ] Wave B3: onboarding wizard + readiness + tools registry
- [ ] Wave C1: agent chat pane (one-brain via API) + Supabase Storage attachments/media
- [ ] Wave C2: agent webview (CDP + C6 consent + C8 trace)
- [ ] Wave C3: comms/notifications + change undo/redo + mobile/PWA polish + cross-surface parity
- [ ] tests (parity + mobile/PWA + negative-access RLS + C6 + cache-safety) green
- [ ] System test on the ECS passed — **owner-gated**

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 (for Leo) | Created FG doc (PLAN) | Leo: build a new mobile-first Next.js `agent-home` on the fixed three-tier stack (Next.js + Python AI layer + Supabase) and move all Phase-2 features into it; existing `web/` is not mobile-friendly. |
| 2026-07-11 | 2 | devin:8cec0d47 (for Leo) | Locked the 4 open decisions (owner-confirmed) | Leo confirmed: (1) bridge C1 principal → server-side Supabase RLS context (GoTrue browser-direct deferred), (2) deploy on-box behind Caddy, (3) coexist — `web/` stays the operator/admin console (option A), (4) `agent-home/` at repo root. Plan is now actionable; Wave A next. |
| 2026-07-21 | 3 | devin:a7a37d33 (for Leo) | **Wave A landed** — `agent-home/` skeleton + auth/data-access seam (A1+A2). See "Seam API surface (Wave A2)" below. | Foundation all later waves consume: mobile-first Next.js 15 App-Router shell (bottom-nav, safe-area, PWA install + offline shell, `data-component` babel plugin) + the BFF seam (C1 principal bridge → server-side Supabase RLS context → typed Python-API client → shared types → RLS-scoped Realtime stub). `web/` untouched; zero new core tools; zero new non-secret `HERMES_*` env vars. |
| 2026-07-21 | 4 | devin:a7a37d33 (for Leo) | **Wave B1 landed** — mobile-first GTS Centre at `/graph` (read-only, C9). | First real feature panel: the `/graph` tab is now a BFF server component that resolves the principal, calls the Python API `GET /api/gts/graph` (C2 + FG-19 `item_grants` RLS enforced upstream), and renders the goal→task→skill hierarchy — engine-computed 0–100 scores, per-node observe/measure evaluation method (never a user-set score), and FG-19 assignment (assignee + read-only watcher count). New: `HermesApiClient.gtsGraph()`; shared types `GtsObservation`, `GtsEvaluationMethod`, `GtsItemGrant`, `GtsSkill`, `GtsGraphResponse` (and `evaluation_method` + `grants` added to `GtsGoal`/`GtsTask`); component `components/gts/GtsCentreView`. Mirrors `web/`'s `GtsCentrePage` as functional reference; the GTS authority logic is **not** re-implemented in TS. **Deviation:** assignment/accept/decline/eval-method **writes are deferred** — no HTTP write API exists (creation/scoring/assignment stay on the CLI/agent authority paths, and `web/`'s panel is likewise read-only), so Wave B1 ships the read surface only. `web/` untouched; zero new core tools / non-secret `HERMES_*` vars; no new Python. |

### Seam API surface (Wave A2) — for Wave B/C consumers

Everything below is server-side (BFF) unless marked client. Import paths are relative to `agent-home/src`.

**C1 principal bridge — session** (`lib/auth/session.ts`)
- `SESSION_COOKIE: "agent_home_session"` — the signed (HMAC-SHA256) HttpOnly cookie name.
- `interface AgentHomeSession { hermesToken: string; principal: Principal; issuedAt: number }`
- `serializeSession(s: AgentHomeSession): string` / `deserializeSession(v: string | undefined): AgentHomeSession | null` (verifies signature, fails closed).
- `readSession(): Promise<AgentHomeSession | null>` · `writeSession(s): Promise<void>` · `clearSession(): Promise<void>` (via `next/headers`).

**Principal resolution** (`lib/auth/principal.ts`)
- `getPrincipal(): Promise<Principal | null>` · `requirePrincipal(): Promise<Principal>` (redirects to `/login`).
- `apiClientForRequest(): Promise<HermesApiClient>` — client bound to the request's bridged token.
- `resolvePrincipalFromToken(hermesToken: string): Promise<Principal | null>` (asks `/api/comms/whoami`).

**Server-side Supabase context — C2/C3** (`lib/supabase/context.ts`)
- `withPrincipalContext<T>(principal, fn: (ctx: PrincipalDbContext) => Promise<T>, opts?: { mode?: StoreMode }): Promise<T>` — opens a READ-ONLY tx, pins `search_path` to the C3 schema (`app_dev`/`app_prod`), and `SET LOCAL`s `hermes.principal_id` / `hermes.principal_role` (mirrors `access.bind_principal`).
- `interface PrincipalDbContext { principal; mode; schema; query<Row>(text, params?): Promise<Row[]> }`
- `scopedSelect<Row>(principal, table, opts?: { columns?; limit?; mode? }): Promise<Row[]>` — ergonomic RLS-scoped read.
- `__setPoolForTests(pool)` — test seam.

**RLS mirror** (`lib/supabase/rls.ts`): `GUC_PRINCIPAL_ID`, `GUC_PRINCIPAL_ROLE`, `scopeReadPolicySql(table): string`.

**Typed Python-API client** (`lib/api/client.ts`)
- `class HermesApiClient` — `new HermesApiClient({ hermesToken?, baseUrl? })`; methods `request<T>(path, init?)`, `whoami()`, `authProviders()`, `gtsGraph()` (Wave B1: `GET /api/gts/graph` → `GtsGraphResponse`), `notifications()`. Replays the bridged token as the `hermes_session_at` cookie + bearer header.
- `class HermesApiError extends Error` (`status`, `body`).

**RLS-scoped Realtime — stub** (`lib/supabase/realtime.ts`, client): `createRealtimeClient(config)`, `subscribeScoped<Row>(client, opts): () => void`, `realtimeEnabled(): boolean` (false until browser-direct GoTrue lands).

**Shared types** (`types/index.ts`): `Role`, `Principal`, `StoreMode`, `Visibility`, `GtsGoal`, `GtsTask`, `GtsNode`, `GtsObservation`, `GtsEvaluationMethod`, `GtsItemGrant`, `GtsSkill`, `GtsGraphResponse` (Wave B1), `InteractionKind`, `TraceRow`, `Tool`, `Notification`.

**Env (secrets in `.env`, deploy-topology `AGENT_HOME_*`):** `AGENT_HOME_SESSION_SECRET`, `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `AGENT_HOME_API_URL` (default `http://127.0.0.1:9119`), `AGENT_HOME_DATASTORE_MODE` (`dev`/`prod`). Zero new non-secret `HERMES_*` vars.

**Routes:** `/login` (bridge login) · `/` (seam proof: principal + one RLS-scoped read) · `/graph` (Wave B1: read-only GTS Centre) · `/chat` `/activity` (tab placeholders) · `POST /api/session/login` · `POST /api/session/logout`.

**Deploy:** on-box behind Caddy — see `agent-home/README.md` (Caddyfile snippet + subdomain plan). Owner-gated; not deployed.

**Deviations (smallest reasonable choices, no FG guidance):** (1) OAuth-provider bridge login is deferred — Wave A ships the password-provider bridge fully and lists providers; the shape is stable. (2) Realtime is a wired stub gated off until browser-direct GoTrue scoping exists (FG explicitly deferred GoTrue). (3) Non-secret deploy topology uses `AGENT_HOME_*` env (a Node server can't read the Python `config.yaml`), never `HERMES_*`.

## Cloud-agent prompt
> **[Phase-3 — after owner confirms Open-decisions §1–4; Wave A first]** Repo `leolau/ai-prentice-4-all`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-20`). Build a **new mobile-first Next.js App-Router app `agent-home/`** (sibling of `web/`) that is the user-facing face of the system and hosts **all Phase-2 features** (GTS Centre + assignment, onboarding, Core-area view, interaction trace, one-brain agent chat, agent webview, tools). Architecture is fixed: **Next.js UI → Python AI layer (`/api/*`) → Supabase (Postgres + Storage + RLS)**. Use a **BFF pattern**: the `agent-home` server (RSC/route handlers) holds the authenticated **C1 principal** context, proxies all agent/authority operations to the **Python API** (one-brain chat, CDP webview, GTS authority writes, onboarding readiness, Core manifest/health, tool enable/promote, comms actions), and does **server-side** Supabase reads with the principal's RLS context (mirror the `hermes.principal_*` GUCs) plus RLS-scoped Realtime for live GTS/trace views. The **browser never** gets a service-role key or bypasses C1/C2/C6/C8. Add **zero** new model tools and **zero** new non-secret `HERMES_*` env vars (behaviour → `config.yaml`). Keep chat on the **one-brain gateway** unchanged (cache-/alternation-safe; traces never injected). Reuse the Nous theme tokens + the `data-component` babel plugin; redesign layouts **mobile-first** (bottom-nav, sheets, touch targets, PWA). **Keep `web/`** as the operator/admin console. Follow `AGENTS.md` (extend-don't-duplicate, footprint ladder). Add parity + mobile/PWA + **negative-access RLS** + C6 + cache-safety tests; keep baseline + web build + `ruff`/`ty` green. Publish the **Wave-A2 auth/data-access foundation as a small interface PR first**, then fan out Wave B (GTS / Core+trace / onboarding+tools) and Wave C (chat / webview / comms+polish) as parallel agents. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist passes** — coordinate with Leo.
