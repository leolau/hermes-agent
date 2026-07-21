/**
 * Real-path RLS integration test for the `agent-home` data seam (FG-20 Wave A).
 *
 * Mirrors the Python E2E style in `tests/` (throwaway Docker Postgres, C3
 * schema, negative-access sanity) but exercises the *TypeScript* seam that the
 * mobile app actually uses: `withPrincipalContext` / `scopedSelect`. It proves:
 *   1. the bridge sets the `hermes.principal_*` GUCs the FORCE'd RLS reads; and
 *   2. an RLS-scoped read returns ONLY the rows a principal may see — an owner
 *      sees all, a member sees `shared` + its own `private:<id>`, and a member
 *      NEVER sees another member's private rows.
 *
 * Reads run as a **non-superuser** role so Postgres actually enforces RLS
 * (superusers bypass it). Skips cleanly when Docker is unavailable.
 */
import { execFileSync } from "node:child_process";

import { Pool } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  __setPoolForTests,
  scopedSelect,
} from "@/lib/supabase/context";
import { scopeReadPolicySql } from "@/lib/supabase/rls";
import type { Principal } from "@/types";

const IMAGE =
  "postgres@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193";

function dockerAvailable(): boolean {
  try {
    execFileSync("docker", ["info"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

const HAVE_DOCKER = dockerAvailable();
const describeMaybe = HAVE_DOCKER ? describe : describe.skip;

function principal(user_id: string, role: Principal["role"]): Principal {
  return {
    user_id,
    display: user_id,
    role,
    channels: [],
    is_owner: role === "owner",
  };
}

interface GoalRow extends Record<string, unknown> {
  id: string;
  visibility: string;
}

describeMaybe("withPrincipalContext RLS enforcement (real Postgres)", () => {
  const container = `agent-home-rls-${Math.random().toString(16).slice(2, 12)}`;
  let adminPool: Pool | undefined;
  let appPool: Pool | undefined;

  beforeAll(async () => {
    execFileSync("docker", ["pull", IMAGE], { stdio: "ignore" });
    execFileSync("docker", [
      "run", "--detach", "--rm", "--name", container,
      "--env", "POSTGRES_PASSWORD=hermes-test",
      "--env", "POSTGRES_DB=hermes_test",
      "--publish", "127.0.0.1::5432", IMAGE,
    ]);
    const portRaw = execFileSync("docker", ["port", container, "5432/tcp"], {
      encoding: "utf8",
    });
    const port = portRaw.trim().split(":").pop();
    const adminDsn = `postgresql://postgres:hermes-test@127.0.0.1:${port}/hermes_test`;

    // Wait for readiness.
    let ready = false;
    for (let i = 0; i < 60 && !ready; i++) {
      const probe = new Pool({ connectionString: adminDsn });
      try {
        await probe.query("SELECT 1");
        ready = true;
      } catch {
        await new Promise((r) => setTimeout(r, 250));
      } finally {
        await probe.end();
      }
    }
    if (!ready) throw new Error("throwaway Postgres did not become ready");

    adminPool = new Pool({ connectionString: adminDsn });
    // C3 schema + a scoped table with the exact C2 read policy from access.py.
    await adminPool.query("CREATE SCHEMA IF NOT EXISTS app_dev");
    await adminPool.query(`
      CREATE TABLE app_dev.goals (
        id TEXT PRIMARY KEY,
        owner_user_id TEXT NOT NULL,
        visibility TEXT NOT NULL,
        title TEXT NOT NULL
      );
    `);
    await adminPool.query(scopeReadPolicySql("app_dev.goals"));
    await adminPool.query(`
      INSERT INTO app_dev.goals (id, owner_user_id, visibility, title) VALUES
        ('g_shared', 'owner', 'shared', 'Shared goal'),
        ('g_owner',  'owner', 'private:owner', 'Owner private'),
        ('g_alice',  'alice', 'private:alice', 'Alice private'),
        ('g_bob',    'bob',   'private:bob',   'Bob private');
    `);
    // Non-superuser role so RLS is actually enforced on scoped reads.
    await adminPool.query(
      "CREATE ROLE agent_home_app LOGIN PASSWORD 'app-pw' NOSUPERUSER",
    );
    await adminPool.query("GRANT USAGE ON SCHEMA app_dev TO agent_home_app");
    await adminPool.query(
      "GRANT SELECT ON ALL TABLES IN SCHEMA app_dev TO agent_home_app",
    );

    appPool = new Pool({
      connectionString: `postgresql://agent_home_app:app-pw@127.0.0.1:${port}/hermes_test`,
    });
    __setPoolForTests(appPool);
  }, 120_000);

  afterAll(async () => {
    __setPoolForTests(undefined);
    await appPool?.end();
    await adminPool?.end();
    try {
      execFileSync("docker", ["rm", "--force", container], { stdio: "ignore" });
    } catch {
      // container already gone
    }
  });

  it("binds the hermes.principal_* GUCs inside the transaction", async () => {
    const { withPrincipalContext } = await import("@/lib/supabase/context");
    const bound = await withPrincipalContext(
      principal("alice", "member"),
      async (ctx) => {
        const rows = await ctx.query<{ id: string; role: string }>(
          "SELECT current_setting('hermes.principal_id', true) AS id, " +
            "current_setting('hermes.principal_role', true) AS role",
        );
        return rows[0];
      },
    );
    expect(bound).toEqual({ id: "alice", role: "member" });
  });

  it("owner sees every row", async () => {
    const rows = await scopedSelect<GoalRow>(principal("owner", "owner"), "goals", {
      columns: "id, visibility",
    });
    expect(rows.map((r) => r.id).sort()).toEqual([
      "g_alice",
      "g_bob",
      "g_owner",
      "g_shared",
    ]);
  });

  it("a member sees shared + own private, never another member's private", async () => {
    const rows = await scopedSelect<GoalRow>(principal("alice", "member"), "goals", {
      columns: "id, visibility",
    });
    const ids = rows.map((r) => r.id).sort();
    expect(ids).toEqual(["g_alice", "g_shared"]);
    expect(ids).not.toContain("g_bob");
    expect(ids).not.toContain("g_owner");
  });

  it("a different member is scoped to its own view (negative access)", async () => {
    const rows = await scopedSelect<GoalRow>(principal("bob", "member"), "goals", {
      columns: "id, visibility",
    });
    expect(rows.map((r) => r.id).sort()).toEqual(["g_bob", "g_shared"]);
  });
});
