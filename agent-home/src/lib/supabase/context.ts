/**
 * Server-side Supabase (Postgres) access context for `agent-home`
 * (FG-20 Wave A2, Decision 1 = BRIDGE).
 *
 * This is the database half of the seam. It mirrors the Python backend's
 * `access.bind_principal` (`hermes_cli/access.py`): open a connection whose
 * `search_path` is pinned to the C3 schema (`app_dev` / `app_prod`), open a
 * transaction, and `SET LOCAL` the two GUCs the FORCE'd RLS policies read
 * (`hermes.principal_id` / `hermes.principal_role`). Every read then runs
 * inside that transaction, so **Postgres RLS** — not app-layer JS filtering —
 * enforces C2 exactly as it does for the Python API today.
 *
 * The browser never reaches this code: it runs only on the `agent-home`
 * server (route handlers / RSC), and never receives a service-role key.
 */
import "server-only";

import { Pool, type PoolClient } from "pg";

import { databaseUrl, datastoreMode, schemaForMode } from "@/lib/env";
import { GUC_PRINCIPAL_ID, GUC_PRINCIPAL_ROLE } from "@/lib/supabase/rls";
import type { Principal, StoreMode } from "@/types";

// A single process-wide pool. Next may reuse the module across requests, so a
// module-level singleton avoids exhausting Postgres connections under load.
let pool: Pool | undefined;

function getPool(): Pool {
  if (!pool) {
    pool = new Pool({ connectionString: databaseUrl() });
  }
  return pool;
}

/** Test-only seam: inject a pool (e.g. one pointed at a throwaway Postgres). */
export function __setPoolForTests(p: Pool | undefined): void {
  pool = p;
}

const IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/;

function assertSchema<T extends string>(schema: T): T {
  if (!IDENT.test(schema)) {
    throw new Error(`agent-home: refusing unsafe schema name ${schema}`);
  }
  return schema;
}

/**
 * The bound context handed to a scoped read: a `pg` client already inside a
 * transaction with the principal's GUCs set. Use `query` for RLS-scoped SQL.
 */
export interface PrincipalDbContext {
  readonly principal: Principal;
  readonly mode: StoreMode;
  readonly schema: "app_dev" | "app_prod";
  query<Row extends Record<string, unknown> = Record<string, unknown>>(
    text: string,
    params?: readonly unknown[],
  ): Promise<Row[]>;
}

/**
 * Run `fn` with a Postgres connection bound to `principal` under the C3 schema.
 *
 * Opens a transaction, pins `search_path`, and `SET LOCAL`s the two GUCs
 * (transaction-scoped, exactly like `access.bind_principal`'s
 * `set_config(..., is_local => true)`), then runs `fn`. The transaction is
 * read-only by default so a scoped *read* can never mutate; commit/rollback
 * and connection release are always handled here.
 *
 * @param principal the resolved C1 principal (id + role) to bind.
 * @param fn        receives a {@link PrincipalDbContext}; return its result.
 * @param opts.mode override the C3 mode (defaults to `datastoreMode()`).
 */
export async function withPrincipalContext<T>(
  principal: Principal,
  fn: (ctx: PrincipalDbContext) => Promise<T>,
  opts: { mode?: StoreMode } = {},
): Promise<T> {
  const mode = opts.mode ?? datastoreMode();
  const schema = assertSchema(schemaForMode(mode));
  const client: PoolClient = await getPool().connect();
  try {
    await client.query("BEGIN READ ONLY");
    // search_path pins the C3 schema; format() is unavailable for SET, so the
    // schema is validated against IDENT above before interpolation.
    await client.query(`SET LOCAL search_path TO ${schema}`);
    // Transaction-scoped GUCs the FORCE'd RLS policies read. Parameterised via
    // set_config so the principal id/role can never break out of the statement.
    await client.query(
      "SELECT set_config($1, $2, true), set_config($3, $4, true)",
      [GUC_PRINCIPAL_ID, principal.user_id, GUC_PRINCIPAL_ROLE, principal.role],
    );
    const ctx: PrincipalDbContext = {
      principal,
      mode,
      schema,
      async query(text, params) {
        const res = await client.query(text, params ? [...params] : undefined);
        return res.rows;
      },
    };
    const result = await fn(ctx);
    await client.query("COMMIT");
    return result;
  } catch (err) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // ignore rollback failures — surface the original error below.
    }
    throw err;
  } finally {
    client.release();
  }
}

/**
 * Typed convenience: RLS-scoped read of a scoped table (owner_user_id +
 * visibility). The RLS policy already filters rows to what `principal` may
 * see; this helper is just the ergonomic seam Wave-B panels call instead of
 * hand-writing `withPrincipalContext` every time.
 *
 * @param table  a validated table name in the C3 schema.
 * @param opts.columns columns to project (default `*`).
 * @param opts.limit   optional row cap.
 */
export async function scopedSelect<
  Row extends Record<string, unknown> = Record<string, unknown>,
>(
  principal: Principal,
  table: string,
  opts: { columns?: string; limit?: number; mode?: StoreMode } = {},
): Promise<Row[]> {
  if (!IDENT.test(table)) {
    throw new Error(`agent-home: refusing unsafe table name ${table}`);
  }
  const columns = opts.columns ?? "*";
  const limit =
    typeof opts.limit === "number" ? ` LIMIT ${Math.max(0, opts.limit | 0)}` : "";
  return withPrincipalContext<Row[]>(
    principal,
    (ctx) => ctx.query<Row>(`SELECT ${columns} FROM ${table}${limit}`),
    { mode: opts.mode },
  );
}
