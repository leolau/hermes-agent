# agent-home

The **mobile-first face** for a Hermes agent (FG-20). It coexists with `web/`
(the desktop operator/admin console stays untouched — FG-20 Decision 3): this
app is what a user opens on their phone.

Architecture is fixed as three tiers (FG-20):

```
Next.js (agent-home, mobile-first UI, this package)
   -> Python (the AI / agent layer + API, /api/* and /auth/*)
   -> Supabase (Postgres + Storage + RLS)
```

`agent-home` is a **BFF** (backend-for-frontend): the browser never talks to
the Python API or Supabase directly, never receives a service-role key, and
never bypasses C1/C2/C6/C8. The server holds the principal session, forwards
authority operations to the Python API, and does server-side Supabase reads
under the principal's RLS context.

## Wave A status

Wave A ships the **skeleton + the auth/data-access seam** only. The feature
panels (GTS graph, one-brain chat, CDP webview, comms) are Waves B/C. The
public seam API is documented in the FG-20 audit log.

## Scripts

```bash
npm run dev        # local dev server (http://localhost:3000)
npm run build      # production build (must pass)
npm run start      # serve the production build
npm run lint       # eslint
npm run typecheck  # tsc --noEmit
npm run test       # vitest (render test + Postgres RLS integration test)
```

Install from the repo root (npm workspaces): `npm install`.

## Environment

See [`.env.example`](./.env.example). Copy to `.env.local` for local dev.
Secrets (`AGENT_HOME_SESSION_SECRET`, `DATABASE_URL`, `SUPABASE_*`) live in
`.env`; non-secret deploy topology is namespaced `AGENT_HOME_*`. Per AGENTS.md
this app adds **zero** new non-secret `HERMES_*` env vars.

## The seam (what Wave B/C consume)

| Concern | Module | Public surface |
| --- | --- | --- |
| C1 principal bridge (session) | `src/lib/auth/session.ts` | `readSession`, `writeSession`, `clearSession`, `AgentHomeSession`, `SESSION_COOKIE` |
| Principal resolution | `src/lib/auth/principal.ts` | `getPrincipal`, `requirePrincipal`, `apiClientForRequest`, `resolvePrincipalFromToken` |
| Server-side Supabase context (C2/C3) | `src/lib/supabase/context.ts` | `withPrincipalContext`, `scopedSelect`, `PrincipalDbContext` |
| Typed Python-API client | `src/lib/api/client.ts` | `HermesApiClient`, `HermesApiError` |
| RLS-scoped Realtime (stub) | `src/lib/supabase/realtime.ts` | `createRealtimeClient`, `subscribeScoped`, `realtimeEnabled` |
| Shared entity types | `src/types/index.ts` | `Principal`, `Role`, `GtsGoal`, `GtsTask`, `GtsNode`, `TraceRow`, `Tool`, `Notification`, … |

## Deploy prep — on-box behind Caddy (FG-20 Decision 2)

> **Owner-gated:** do NOT deploy to prod or touch the running box. This is the
> documented plan + config snippet only.

`agent-home` runs as a long-lived Next.js server on the **same box** as the
Python AI layer and the **same Supabase**, fronted by the existing Caddy.

1. **Build & run** (as the deploy user, in `agent-home/`):

   ```bash
   npm ci --workspace agent-home
   npm run --prefix agent-home build
   # served by `next start` (default :3000) under a process manager
   # (systemd/pm2), with the .env from `.env.example` populated.
   PORT=3000 npm run --prefix agent-home start
   ```

2. **Subdomain** (recommended): serve the app at `home.<domain>`. This is a BFF:
   the browser only ever talks to the Next server, which calls the Python AI
   layer **server-side** (via `AGENT_HOME_API_URL`). So all public traffic on
   this origin — including agent-home's own `/api/session/*` route handlers —
   goes to Next; the Python API is never exposed here. Caddyfile:

   ```caddy
   home.example.com {
       encode zstd gzip
       reverse_proxy 127.0.0.1:3000
   }
   ```

   Then set `AGENT_HOME_API_URL=http://127.0.0.1:9119` and
   `AGENT_HOME_DATASTORE_MODE=prod` in the app's `.env`. Do **not** split
   `/api/*` to the Python layer here — that would shadow the login/logout
   bridge routes the Next server owns.

   A ready-to-use unit + Caddy block + step-by-step runbook for the actual prod
   box live in [`deploy/`](./deploy/) (`agent-home.service`,
   `Caddyfile.agent-home`, `DEPLOY.md`).

3. **Sub-path alternative** (if a subdomain is undesirable): mount under
   `/home` on the existing site and set Next's `basePath: "/home"`. A subdomain
   is preferred because the service worker scope and cookie domain stay clean.

Notes:
- The service worker never caches `/api/*` or `/auth/*` (per-principal data).
- TLS/HTTP-to-HTTPS is Caddy's automatic cert; the session cookie is `Secure`
  in production (`NODE_ENV=production`).
