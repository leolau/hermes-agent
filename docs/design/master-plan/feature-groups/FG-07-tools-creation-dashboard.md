# FG-07 — Tools creation & configuration + Dashboard

**Wave:** 2 · **Owner agent:** _unassigned_ · **Status:** Not started

## Summary
Let users/agents **create and configure tools** and manage them from a
**dashboard**. A "tool" here is primarily an **in-house system** (D3): a new
capability, default **Next.js + one Node process**, exposing a **web UI (human)
+ MCP (agent)**. Tools have lifecycle (dev→prod, enable/disable), config, and
scoped ownership; the dashboard is the human control surface.

## Decisions applied
- D3 (in-house tool default stack + dual interface), D5 (dev→prod), D1/C2 (tool ownership/visibility), C5/C6 (changes + approval), footprint ladder (tools are MCP-exposed, not new core tools).

## Reuse map
- `hermes_cli/tools_config.py` — toolset toggles + auto-enable-on-credential; extend for user-created tools.
- `hermes mcp add/install/serve` + catalog — register the tool's MCP.
- `web/` (React 19 + TS + Vite + Tailwind + Nous UI) — dashboard; the existing `hermes_cli/web_server.py` backend.
- FG-11 MCP surface + `mcp_endpoints` registry; FG-12 change log.

## Design / approach
1. **Tool registry** (`app_*`): `tools(id, name, owner_user_id, visibility,
   kind ∈ {in_house, remote, builtin}, stack, mode, status, mcp_endpoint_ref,
   web_url, config_json)`.
2. **In-house scaffolder:** `hermes tool new <name>` (CLI+skill) scaffolds a
   Next.js app (own Node process, own port) with a **web UI** and a **thin MCP
   server** (FG-11 client registers it). New components follow the repo
   **`data-component="ComponentName"`** convention for inspectable previews.
3. **Config UX:** behavioural config in the tool's `config_json` / `config.yaml`
   — **never new `HERMES_*` env vars** (secrets only). Auto-enable when a
   required credential is present (existing pattern).
4. **Dashboard:** manage tools (list/enable/disable/configure/promote), show
   status/health, link to the tool's web UI, and render the FG-12 change log.
5. **Lifecycle:** tools start in **dev**, promoted via FG-13; every change emits
   C5; risky ops gated by C6.

## Dev/Prod + Supabase
Tool registry + config in `app_*` (mode-scoped). Dev tools only run/reachable in
dev sessions.

## Testing requirements
- Unit: registry CRUD + scope; scaffolder output shape; config validation (no `HERMES_*` non-secret).
- Negative access: private tool invisible cross-user; owner sees.
- E2E: scaffold an in-house tool → runs its own Node process → web UI + MCP both reachable → register endpoint → enable → promote dev→prod (approval + change-event).
- Baseline green.

## Dependencies
- **Blocked by:** FG-13 (C3), FG-01 (C2), FG-11 (MCP registration), FG-12 (C5/C6).
- **Blocks:** FG-08 (in-house build path), FG-09, FG-10 (shares dashboard).

## Definition of Done
Tests green + baseline green + `ruff`/`ty` (Python) + web lint/typecheck clean; scaffolded tool exposes both web UI + MCP; dashboard manages lifecycle; no new non-secret env vars; `data-component` convention applied to new web components.

## Progress checklist
- [ ] Tool registry (scope/mode/status)
- [ ] `hermes tool new` Next.js scaffolder (web UI + MCP, own Node process)
- [ ] Dashboard: manage/config/promote + health + change-log view
- [ ] Config UX (config.yaml/config_json, no HERMES_* non-secret)
- [ ] tests (unit + negative + E2E) green

## Audit log
| Date | Edition | Author | Change | Rationale |
|------|---------|--------|--------|-----------|
| 2026-07-11 | 1 | devin:8cec0d47 | Created FG doc | Plan kickoff |

## Cloud-agent prompt
> **[Wave 2 — start after FG-11 + FG-12 merge]** Repo `leolau/hermes-agent`, branch off `develop`. Read `docs/design/master-plan/README.md` and this doc (`FG-07`). Implement **tool creation + configuration + dashboard**. Add a `mode`-aware, scope-aware (contract C2) **tool registry** in Supabase `app_*`. Build `hermes tool new <name>` (CLI+skill) that scaffolds an **in-house tool = Next.js app in its own Node process** exposing BOTH a **web UI** and a **thin MCP server** (registered via FG-11). Behavioural config lives in `config.yaml`/`config_json` — **never new `HERMES_*` env vars** (secrets only; auto-enable on credential presence per existing pattern). Extend the `web/` dashboard (React 19/Vite/Tailwind/Nous UI via `hermes_cli/web_server.py`) to list/enable/disable/configure/promote tools, show health, link the tool's web UI, and render the FG-12 change log. Tools start in **dev**, promote via FG-13 (approval + change-event C5/C6). New web components MUST carry `data-component="ComponentName"` on their root element. Follow `AGENTS.md` (tools are MCP-exposed, NOT new core tools; footprint ladder). Add unit + negative-access + E2E tests + web lint/typecheck; run `scripts/run_tests.sh`, `ruff`, `ty`. Edit ONLY this FG doc. Open a PR linking this doc.
