/**
 * SQL fragments that mirror the Python C2 RLS model
 * (`hermes_cli/access.py`). `agent-home` never *defines* the production
 * policies — the Python backend owns the schema and installs the FORCE'd RLS
 * (`apply_scope_rls`, `apply_item_grants_rls`, the interactions policy). These
 * constants exist so:
 *   1. the server-side Supabase context binds the exact same GUCs
 *      (`hermes.principal_id` / `hermes.principal_role`) the policies read, and
 *   2. the negative-access integration test can stand up an equivalent schema
 *      on a throwaway Postgres and prove the seam enforces the boundary.
 *
 * Keep these byte-for-byte faithful to `access.py`; a drift here is a security
 * bug, so the integration test asserts the observable behaviour end-to-end.
 */

/** The GUC the read policy keys the principal id off (mirror of `access._GUC_ID`). */
export const GUC_PRINCIPAL_ID = "hermes.principal_id";
/** The GUC the read policy keys the principal role off (mirror of `access._GUC_ROLE`). */
export const GUC_PRINCIPAL_ROLE = "hermes.principal_role";

/**
 * The C2 read predicate a `FORCE`d `SELECT` policy applies to a scoped table
 * (mirror of `access.apply_scope_rls`): owner role sees all; everyone sees
 * `shared`; a member additionally sees its own `private:<user_id>` rows.
 */
export function scopeReadPolicySql(table: string): string {
  return `
    ALTER TABLE ${table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE ${table} FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS hermes_scope_read ON ${table};
    CREATE POLICY hermes_scope_read ON ${table}
        FOR SELECT
        USING (
            current_setting('${GUC_PRINCIPAL_ROLE}', true) = 'owner'
            OR visibility = 'shared'
            OR visibility = 'private:' || current_setting('${GUC_PRINCIPAL_ID}', true)
        );
  `;
}
