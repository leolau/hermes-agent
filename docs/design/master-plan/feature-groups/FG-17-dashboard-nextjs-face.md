# FG-17 — Dashboard as the system "face" (Next.js) + embedded Telegram + agent webview

**Wave:** B→C (Phase-2) · **Owner agent:** _unassigned_ · **Status:** Not started

## Summary
Make the dashboard the **face of the system** and standardize it on **Next.js**
(Req 17.0). Migrate the existing **Vite** frontend to **Next.js (App Router)**
while **keeping the existing Python backend as the API layer** (frontend-only
migration — the lower-risk path Leo chose). The **Core system is always present
on the dashboard** (Core-area view, GTS Centre, change/trace views). Add an
**embedded Telegram chat** so a user can talk to the same backend agent from
either the **Telegram app or the dashboard**, an agent-controllable
**webview** so the agent can drive any webpage **on behalf of the user**
(consent + audit gated), and **link/icon registration** for user/agent-created
tools (which stay **Next.js + Node**, per D3).

## Decisions applied
- **D12 — Dashboard standardizes on Next.js (App Router) frontend over the existing Python API backend (frontend-only migration). All new in-house tools remain Next.js + Node (D3).**
- **D13 — Telegram is both a native channel (app) and the dashboard-embedded conversational UI; both hit the same one-brain backend (FG-03).**
- C6 (agent webview actions on behalf of a user are consent/approval-gated), C7 (dashboard/core code is Core — the runtime agent can't edit it), C8 (all dashboard + webview actions traced), C2 (every view is scope-filtered; owner sees all).

## Reuse map
- `web/` (React 19 + TS + Tailwind + Nous UI, Vite) — **port** its pages/components to Next.js App Router; keep the component/theming system.
- `hermes_cli/web_server.py` + existing `/api/*` routes — **kept as the backend**; Next.js calls them (reuse every contract: FG-07 tools, FG-10 comms, dashboard_auth). No backend rewrite.
- `hermes_cli/dashboard_auth/` — reused for the Next.js frontend's auth.
- Existing **browser toolset** (`toolsets` browser/CDP at `http://localhost:29229`) — the engine behind the agent webview; the dashboard surfaces a live view (CDP screencast) + control.
- FG-07 tool registry + scaffolder (`hermes tool new`, Next.js+Node) — link/icon registration reuses this.
- FG-16 (C8) trace view, FG-12 (C5) change-log view, FG-14 (C7) Core-area view, FG-18/19 GTS Centre — rendered as dashboard surfaces.

## Design / approach
1. **Frontend migration (parity first).** Stand up a Next.js (App Router) app
   that reproduces today's dashboard **feature-for-feature** against the SAME
   Python `/api/*` backend, behind a parity checklist; re-run FG-07 + FG-10
   system tests. No API/data-model changes. Keep Nous-UI theming.
2. **Core-area view.** A permanent dashboard section shows the **Core** (FG-14
   manifest, version/health, change log FG-12, trace FG-16) — read-only,
   reflecting that Core is immutable to agent/user.
3. **Embedded Telegram chat.** Embed a web Telegram surface (official Telegram
   Login/Web widget or a thin server-brokered chat bound to the same bot) so a
   user can converse in-dashboard; messages route through the **same FG-03
   one-brain gateway** as the Telegram app. (If the official web-widget's
   auth/security constraints prevent true embedding, fall back to a
   dashboard-native chat pane that posts to the same backend session — decision
   recorded in this doc when implemented.)
4. **Agent webview (on behalf of user).** A dashboard pane hosts a
   controllable browser view backed by the existing CDP browser; the agent can
   navigate/act on a webpage for the user. **Every** such action is
   **consent/approval-gated (C6)** and **traced (C8)**; per-user browser
   profiles/cookies are isolated; destructive/credentialed actions require
   explicit approval. This is a high-risk surface — default-deny, opt-in.
5. **Tool link/icon registration.** User/agent-created tools (Next.js+Node via
   FG-07) register a link/icon/route in the dashboard nav; enable/disable/
   configure/promote (dev→prod, FG-13) + health from the existing registry.
6. **Core protection.** The dashboard + its backend are **Core (C7)** — the
   runtime agent cannot edit them; only human devs via PR.

## Data model
- No new backend schema for the migration (reuses existing `/api/*` + tables).
- Webview: `browser_sessions(id, user_id, profile_ref, ...)` (per-user isolation) + C8 traces for each action; reuse FG-07 `tools` for registration.

## Dev/Prod + Supabase
Backend keeps its dev/prod routing (C3). Dashboard shows mode; promotions run
through FG-13. Channels (incl. embedded Telegram) are prod-only per D5.

## Testing requirements
- **Parity tests (required):** every existing dashboard feature (FG-07 tools page, FG-10 comms, auth) works identically on Next.js against the unchanged Python API — re-run FG-07 + FG-10 acceptance.
- Web lint/typecheck (Next.js) green; `data-component` attribute standard applied to components.
- Unit/integration: embedded-Telegram message reaches the same backend session as the app; webview action is **blocked without consent** and **allowed+traced with consent** (C6/C8); per-user browser profile isolation.
- Negative access: a user's webview/trace/GTS views are scope-filtered; owner sees all.
- E2E: bring up the Next.js dashboard on the stack; converse via embedded Telegram → same brain; register a scaffolded tool → icon appears + opens its Next.js UI.

## System testing (system-test box)
**Required after this FG's development completes** (part of DoD), on the new ECS (`hermes-systest`, 4/16, EIP `47.83.199.25`) against **staging** (`app_dev`) (**never prod**). See README §7.1. Checklist:
- Deploy the Next.js dashboard on the box; **re-run FG-07 + FG-10 system-test checklists** and confirm parity (no regression).
- Converse via the **embedded Telegram** pane and confirm it hits the same one-brain backend as the Telegram app (same session/C4 key).
- Drive the **agent webview** on a real page: an action is refused without consent and succeeds **with** approval, fully traced (C8); per-user profile isolation holds.
- Register a scaffolded Next.js+Node tool → link/icon appears and opens the tool UI; dev→prod promote works.
- **Gate:** not complete/promotable until this checklist passes.

## Dependencies
- **Blocked by:** FG-07 (tool registry/dashboard), FG-10 (comms), FG-01 (auth/scope), FG-12 (C6 approval), FG-16 (C8 trace), FG-14 (C7). Renders FG-18/19 (GTS Centre) + FG-15 (first-run wizard).
- **Blocks:** the dashboard surfaces of FG-15/16/18/19.

## Definition of Done
Parity tests green (FG-07/10 re-run) + web lint/typecheck + baseline + `ruff`/`ty` clean; Next.js App Router frontend over the unchanged Python API; Core-area view, embedded Telegram (same brain), consent+traced agent webview, and tool link/icon registration all working; dashboard code marked Core (C7); **ECS system test green**.

## Progress checklist

**FG-17a — frontend parity migration (this milestone):**
- [x] Next.js (App Router) frontend parity port over the existing Python `/api/*` backend (reuse dashboard_auth, Nous UI) — `web/` migrated Vite → Next.js static export into `hermes_cli/web_dist/`; `hermes_cli/web_server.py` + every `/api/*` contract left unchanged
- [x] `data-component="ComponentName"` standard applied to every React component root via a build-time Babel plugin (`web/tools/babel-plugin-data-component.cjs`) — verified live in the rendered DOM
- [x] Parity re-verified: FG-07 Tools page and FG-10 Comms page render feature-for-feature identically; all routes, dynamic plugin nav, theme/language, auth/session bootstrap, and forwarded-prefix root-path serving preserved
- [x] tests green: web lint (0 errors) + typecheck + vitest unit; backend `tests/plan_baseline/` + `tests/hermes_cli/test_web_server.py` + `test_web_ui_build.py`; `ruff`/`ty` show no new diagnostics vs `develop`
- [x] **Follow-up (17a):** reverse-proxy *path-prefix* serving of Next's `/_next/*` assets — **thin-proxy fix (Leo's choice)**: `web_server.py`'s `_serve_index()` now re-bases every `/_next/*` reference (script/link entry tags **and** the embedded `self.__next_f` RSC flight payload) under `X-Forwarded-Prefix`, reusing the same prefix-rewrite path as the Vite-era `/assets|/fonts|/ds-assets|/favicon` paths; a new `/_next/static/css/*.css` interceptor rewrites absolute `url(/_next/static/media/…)` + public `url(/fonts-terminal/…)` refs (mirroring the legacy `/assets/*.css` interceptor). The frontend build sets webpack `output.publicPath = "auto"` (`web/next.config.mjs`) so the runtime derives its chunk base from the (rewritten) runtime-chunk URL — lazily-loaded `/_next/*` chunks resolve under `<prefix>/_next/…` with no per-prefix rebuild. Root-path (no-prefix) serving is byte-identical. Covered by `tests/hermes_cli/test_web_server.py::TestForwardedPrefixNextAssets` (root unchanged + prefixed tags/CSS/chunk resolution).

**FG-17b — new panels (later wave, NOT in this PR):**
- [ ] Core-area view (FG-14 manifest/health + FG-12 change log + FG-16 trace)
- [ ] Embedded Telegram chat → same FG-03 one-brain backend
- [ ] Agent webview (CDP) — consent-gated (C6) + traced (C8) + per-user profile isolation
- [ ] Tool link/icon registration (FG-07 registry; Next.js+Node tools) + dev→prod promote
- [ ] tests (17b unit + negative + E2E) green
- [ ] System test on the system-test ECS passed (see *System testing* section) — **Leo-owned gated step; not run by the migration session**

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-12 | 1 | devin:8cec0d47 (for Leo) | Created FG doc | Phase-2 req 17.0: dashboard = the face; standardize on Next.js (frontend-only migration), embedded Telegram, agent webview, tool registration |
| 2026-07-12 | 2 | devin:b33d3665 (for Leo) | FG-17a parity migration: `web/` ported Vite → Next.js (App Router, static export) over the unchanged Python `/api/*` backend; Nous UI/theming, dashboard_auth, plugin loading and all routes reused as-is; `data-component` standard automated via build-time Babel plugin; parity re-verified (FG-07 Tools + FG-10 Comms) with before/after screenshots. Backend (`web_server.py`, `/api/*`) untouched. FG-17b panels + ECS system test + prod promotion deliberately NOT done (later wave / Leo-owned gates). | Wave-B migration milestone: land the frontend parity face first with no backend/API regression |
| 2026-07-13 | 3 | devin:bf7c10dd (for Leo) | FG-17a follow-up (thin-proxy fix): `web_server.py` `_serve_index()` + a new `/_next/static/css/*.css` interceptor now re-base Next's `/_next/*` entry tags, RSC flight payload, and CSS `url(/_next/static/media/…)`/`url(/fonts-terminal/…)` refs under `X-Forwarded-Prefix` (reusing the existing prefix-rewrite path); `web/next.config.mjs` sets webpack `output.publicPath = "auto"` so runtime chunk loads resolve under the prefix. Root-path serving byte-identical; tests added (`TestForwardedPrefixNextAssets`). ECS system test + prod promotion still Leo-owned gates — NOT run. | Prefixed (`/hermes`) deploys couldn't load Next's JS/CSS chunks; backend/build-config change was out of the parity PR's frontend-only scope |

## Cloud-agent prompt
> **[Phase-2 Wave B (migration) → Wave C (new panels) — start migration after Phase-1 develop merged; new panels after FG-14/16/18 land]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-17`). **Migrate the dashboard frontend `web/` from Vite to Next.js (App Router), KEEPING the existing Python backend (`hermes_cli/web_server.py` + `/api/*`) as the API layer** — reuse every existing API contract, `dashboard_auth`, and the Nous-UI components; achieve feature parity FIRST (re-run FG-07 + FG-10 acceptance, no regression). Then add: a permanent **Core-area view** (FG-14 manifest/health + FG-12 change log + FG-16 trace); an **embedded Telegram chat** that routes to the SAME FG-03 one-brain backend as the Telegram app; an **agent webview** backed by the existing CDP browser toolset so the agent can drive webpages **on behalf of the user**, with **every action consent/approval-gated (C6) + traced (C8)** and per-user browser-profile isolation (default-deny, opt-in); and **tool link/icon registration** for FG-07 Next.js+Node tools (enable/disable/configure/promote). The dashboard + backend are **Core (C7)** — not agent-editable. Follow `AGENTS.md` and the `data-component` standard. Add parity + web lint/typecheck + unit + negative-access + E2E tests; keep baseline green; run `scripts/run_tests.sh`, `ruff`, `ty`, and the web checks. Edit ONLY this FG doc. Open a PR linking this doc. **Not done until this FG's *System testing (system-test box)* checklist passes** — coordinate with Leo.
