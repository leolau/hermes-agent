import { MobileShell } from "@/components/MobileShell";
import { ToolsRegistry } from "@/components/tools/ToolsRegistry";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { ToolsResponse } from "@/types";

export const dynamic = "force-dynamic";

const EMPTY: ToolsResponse = { configured: false, mode: "prod", tools: [] };

/**
 * FG-20 Wave B3 — tool registry. BFF: resolves the principal and reads the
 * FG-07 registry (`/api/tools`, C2-scoped upstream). Read-only — enable/config/
 * promote stay on the operator authority paths. Reached from Home.
 */
export default async function Page() {
  await requirePrincipal();

  let data = EMPTY;
  let error: string | null = null;
  try {
    const client = await apiClientForRequest();
    data = await client.tools();
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load tools";
  }

  return (
    <MobileShell title="Tools">
      {error ? (
        <div
          data-component="ToolsError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load the tool registry ({error}).
        </div>
      ) : (
        <ToolsRegistry data={data} />
      )}
    </MobileShell>
  );
}
