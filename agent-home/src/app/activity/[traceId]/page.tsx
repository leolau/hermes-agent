import { TraceDetail } from "@/components/activity/TraceDetail";
import { MobileShell } from "@/components/MobileShell";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { TraceDetailResponse } from "@/types";

export const dynamic = "force-dynamic";

/**
 * FG-20 Wave B2 — one C8 trace's event timeline. BFF: resolves the principal
 * and calls the Python API `/api/comms/traces/{id}` (C2-scoped upstream).
 */
export default async function Page({
  params,
}: {
  params: Promise<{ traceId: string }>;
}) {
  await requirePrincipal();
  const { traceId } = await params;

  let detail: TraceDetailResponse = {
    configured: false,
    trace_id: traceId,
    interactions: [],
    rollup: null,
  };
  let error: string | null = null;
  try {
    const client = await apiClientForRequest();
    detail = await client.trace(traceId);
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load trace";
  }

  return (
    <MobileShell title="Trace">
      {error ? (
        <div
          data-component="TraceDetailError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load this trace ({error}).
        </div>
      ) : (
        <TraceDetail detail={detail} />
      )}
    </MobileShell>
  );
}
