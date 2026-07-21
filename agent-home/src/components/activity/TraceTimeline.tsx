import Link from "next/link";

import { Pill } from "@/components/ui/Pill";
import type { TraceSummary } from "@/types";

/**
 * FG-20 Wave B2 — mobile-first interaction-trace timeline (read-only, C8).
 *
 * Lists the C2-scoped C8 traces the Python ledger returns from
 * `/api/comms/traces`: one card per conversation/trace with its platform,
 * event count, per-kind breakdown, and span. Tapping a trace opens its
 * event-level timeline at `/activity/{trace_id}`. Read-only — the ledger is
 * an append-only audit surface.
 */

function fmt(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export function TraceTimeline({
  traces,
  configured,
}: {
  traces: TraceSummary[];
  configured: boolean;
}) {
  if (!configured) {
    return (
      <div data-component="TraceTimeline" className="text-sm text-[var(--color-muted)]">
        Trace ledger not configured (needs the application datastore).
      </div>
    );
  }

  if (traces.length === 0) {
    return (
      <div data-component="TraceTimeline" className="text-sm text-[var(--color-muted)]">
        No traces visible in your scope yet.
      </div>
    );
  }

  return (
    <div data-component="TraceTimeline" className="flex flex-col gap-2">
      {traces.map((trace) => (
        <Link
          key={trace.trace_id}
          href={`/activity/${encodeURIComponent(trace.trace_id)}`}
          data-component="TraceCard"
          className="block rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 active:opacity-70"
        >
          <div className="flex flex-wrap items-center gap-2">
            {trace.platform ? <Pill tone="accent">{trace.platform}</Pill> : null}
            <Pill tone="muted">{trace.event_count} events</Pill>
            {trace.rolled_up ? <Pill tone="muted">rolled up</Pill> : null}
            <span className="text-xs text-[var(--color-muted)]">
              {trace.actor_user_id ?? "—"}
            </span>
          </div>
          {Object.keys(trace.kind_counts).length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {Object.entries(trace.kind_counts).map(([kind, n]) => (
                <span
                  key={kind}
                  className="rounded-full bg-[var(--color-surface-2)] px-2 py-0.5 text-xs text-[var(--color-muted)]"
                >
                  {kind} · {n}
                </span>
              ))}
            </div>
          ) : null}
          <p className="mt-2 text-xs text-[var(--color-muted)]">{fmt(trace.last_ts)}</p>
        </Link>
      ))}
    </div>
  );
}
