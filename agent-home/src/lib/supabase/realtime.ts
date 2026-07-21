/**
 * RLS-scoped Supabase Realtime helper (FG-20 Wave A2 — thin, tested stub).
 *
 * Later waves (GTS graph, interaction trace) want *live* views. Those must be
 * RLS-scoped: the browser subscribes with the Supabase **anon** key and
 * Realtime honours the same row-level-security policies the server-side reads
 * do — the browser never gets a service-role key and can never widen its own
 * visibility. This module is intentionally minimal for Wave A: it constructs a
 * correctly-configured client and a scoped channel factory, so Wave-B panels
 * plug their table + callback in without re-deriving the security posture.
 *
 * NOTE: Realtime RLS requires the anon role's JWT to carry the principal's
 * claims (a Supabase GoTrue concern). FG-20 Decision 1 deferred browser-direct
 * GoTrue, so until that lands this helper is wired but the *subscription* is
 * gated behind {@link realtimeEnabled}. The shape is stable so Wave B can
 * enable it without an API change.
 */
"use client";

import {
  createClient,
  REALTIME_LISTEN_TYPES,
  type RealtimeChannel,
  type RealtimePostgresChangesPayload,
  type SupabaseClient,
} from "@supabase/supabase-js";

export interface RealtimeConfig {
  /** Supabase project URL (browser-safe). */
  url: string;
  /** Supabase anon key (browser-safe; RLS enforces access). */
  anonKey: string;
}

/**
 * Whether live subscriptions should actually be opened. Off until the
 * browser-direct GoTrue bridge (FG-20 Decision 1 follow-up) lands, because
 * without a principal-scoped JWT the anon subscription cannot be RLS-scoped.
 */
export function realtimeEnabled(): boolean {
  return false;
}

/** Build an RLS-scoped Supabase client for the browser (anon key only). */
export function createRealtimeClient(config: RealtimeConfig): SupabaseClient {
  return createClient(config.url, config.anonKey, {
    auth: { persistSession: false, autoRefreshToken: false },
    realtime: { params: { eventsPerSecond: 5 } },
  });
}

export interface ScopedChannelOptions<Row extends Record<string, unknown>> {
  /** The C3 schema (`app_dev` / `app_prod`) the table lives in. */
  schema: "app_dev" | "app_prod";
  /** The table to watch (RLS on the table scopes what rows arrive). */
  table: string;
  /** Called for each RLS-permitted change (INSERT/UPDATE/DELETE). */
  onChange: (row: Row) => void;
}

/**
 * Subscribe to RLS-scoped changes on one table. Returns an unsubscribe fn.
 *
 * When {@link realtimeEnabled} is false the subscription is a no-op (returns a
 * no-op unsubscribe) so callers can wire it unconditionally today and it comes
 * alive once GoTrue scoping is in place — no call-site change needed.
 */
export function subscribeScoped<Row extends Record<string, unknown>>(
  client: SupabaseClient,
  options: ScopedChannelOptions<Row>,
): () => void {
  if (!realtimeEnabled()) {
    return () => {};
  }
  const channel: RealtimeChannel = client
    .channel(`agent-home:${options.schema}.${options.table}`)
    .on<Row>(
      REALTIME_LISTEN_TYPES.POSTGRES_CHANGES,
      { event: "*", schema: options.schema, table: options.table },
      (payload: RealtimePostgresChangesPayload<Row>) => {
        const row = payload.new;
        if (row && Object.keys(row).length > 0) {
          options.onChange(row as Row);
        }
      },
    )
    .subscribe();
  return () => {
    void client.removeChannel(channel);
  };
}
