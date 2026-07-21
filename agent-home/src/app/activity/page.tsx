import { TraceTimeline } from "@/components/activity/TraceTimeline";
import { MobileShell } from "@/components/MobileShell";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { TracesResponse } from "@/types";

export const dynamic = "force-dynamic";

const EMPTY: TracesResponse = { configured: false, traces: [] };

/**
 * FG-20 Wave B2 — Activity tab: the C8 interaction-trace timeline. BFF: the
 * server resolves the principal and calls the Python API `/api/comms/traces`
 * (C2-scoped upstream), then renders the mobile timeline. Read-only.
 */
export default async function Page() {
  await requirePrincipal();

  let data = EMPTY;
  let error: string | null = null;
  try {
    const client = await apiClientForRequest();
    data = await client.traces(50);
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load traces";
  }

  return (
    <MobileShell title="Activity">
      {error ? (
        <div
          data-component="ActivityError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load the interaction trace ({error}).
        </div>
      ) : (
        <TraceTimeline traces={data.traces} configured={data.configured} />
      )}
    </MobileShell>
  );
}
