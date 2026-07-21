/**
 * Server-side environment access for `agent-home` (FG-20 Wave A).
 *
 * Env-var policy (per AGENTS.md + FG-20): `agent-home` introduces **zero new
 * non-secret `HERMES_*` env vars**. The only env this app reads is either:
 *   - a real **secret** (DB DSN, Supabase anon key, the session-signing
 *     secret) — these belong in `.env`, exactly like a Supabase key; or
 *   - **deploy topology** for this Node server (the Python API base URL, the
 *     Supabase project URL, the datastore mode), namespaced `AGENT_HOME_*` so
 *     it never collides with or extends the Python `HERMES_*` namespace.
 *
 * All accessors are lazy so `next build` succeeds on a box with nothing
 * configured; a value is only *required* at request time by the helper that
 * needs it, which then fails loudly with an actionable message.
 */
import type { StoreMode } from "@/types";

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `agent-home: missing required environment variable ${name}. ` +
        `See agent-home/.env.example.`,
    );
  }
  return value;
}

/** HMAC secret used to sign the `agent-home` session cookie. Required (secret). */
export function sessionSecret(): string {
  return required("AGENT_HOME_SESSION_SECRET");
}

/**
 * Base URL of the Python AI layer (`/api/*`, `/auth/*`). Deploy topology,
 * defaults to the on-box loopback the current prod Caddy fronts.
 */
export function hermesApiBaseUrl(): string {
  return (process.env.AGENT_HOME_API_URL || "http://127.0.0.1:9119").replace(
    /\/+$/,
    "",
  );
}

/** Postgres DSN for server-side Supabase reads. Required at read time (secret). */
export function databaseUrl(): string {
  // `DATABASE_URL` is the same secret the Python backend points
  // `datastore.supabase_app.dsn` at; reuse it rather than minting a new name.
  return required("DATABASE_URL");
}

/** Supabase project URL (for RLS-scoped Realtime). Deploy topology. */
export function supabaseUrl(): string {
  return required("SUPABASE_URL");
}

/** Supabase anon key (browser-safe; RLS enforces access). Secret-ish. */
export function supabaseAnonKey(): string {
  return required("SUPABASE_ANON_KEY");
}

/**
 * C3 datastore mode. Dashboard/CLI-style surfaces default to `dev`; the prod
 * deploy sets `AGENT_HOME_DATASTORE_MODE=prod`. Never invents a third mode.
 */
export function datastoreMode(): StoreMode {
  const raw = (process.env.AGENT_HOME_DATASTORE_MODE || "dev").trim();
  if (raw === "prod") return "prod";
  if (raw === "dev") return "dev";
  throw new Error(
    `agent-home: invalid AGENT_HOME_DATASTORE_MODE '${raw}'; expected 'dev' or 'prod'.`,
  );
}

/** The Postgres schema for the resolved mode (contract C3). */
export function schemaForMode(mode: StoreMode): "app_dev" | "app_prod" {
  return mode === "prod" ? "app_prod" : "app_dev";
}
