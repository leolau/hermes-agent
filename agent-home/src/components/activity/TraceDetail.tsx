import Link from "next/link";

import { Pill } from "@/components/ui/Pill";
import type { Tone } from "@/components/ui/Pill";
import type { TraceDetailResponse, TraceRow } from "@/types";

/**
 * FG-20 Wave B2 — one C8 trace's event-level timeline (read-only, C2-scoped).
 *
 * Renders the ordered interaction events the Python ledger returns from
 * `/api/comms/traces/{id}`: each `inbound`/`turn`/`tool_call`/`tool_result`/
 * `outbound`/`error`/… step with its kind, ref, and summary, plus the rolled-up
 * span. Nested tool events are indented under their parent turn.
 */

const KIND_TONE: Record<string, Tone> = {
  inbound: "accent",
  turn: "muted",
  tool_call: "muted",
  tool_result: "muted",
  outbound: "success",
  approval: "warning",
  change: "warning",
  cost: "muted",
  error: "danger",
  core_denied: "danger",
};

function fmt(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString();
}

function EventRow({ event }: { event: TraceRow }) {
  const nested = event.kind === "tool_call" || event.kind === "tool_result";
  return (
    <li
      data-component="TraceEventRow"
      className={nested ? "pl-4" : ""}
    >
      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Pill tone={KIND_TONE[event.kind] ?? "muted"}>{event.kind}</Pill>
          <span className="text-xs text-[var(--color-muted)]">{fmt(event.ts)}</span>
        </div>
        {event.summary ? <p className="mt-1 text-sm">{event.summary}</p> : null}
        {event.ref ? (
          <code className="mt-1 block truncate text-xs text-[var(--color-muted)]">
            {event.ref}
          </code>
        ) : null}
      </div>
    </li>
  );
}

export function TraceDetail({ detail }: { detail: TraceDetailResponse }) {
  if (!detail.configured) {
    return (
      <div data-component="TraceDetail" className="text-sm text-[var(--color-muted)]">
        Trace ledger not configured (needs the application datastore).
      </div>
    );
  }

  const { rollup, interactions } = detail;

  return (
    <div data-component="TraceDetail" className="flex flex-col gap-3">
      <Link
        href="/activity"
        className="text-sm text-[var(--color-muted)] active:opacity-70"
      >
        ‹ All traces
      </Link>

      <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
          Trace
        </p>
        <code className="mt-1 block break-all text-sm">{detail.trace_id}</code>
        {rollup ? (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {rollup.platform ? <Pill tone="accent">{rollup.platform}</Pill> : null}
            <Pill tone="muted">{rollup.event_count} events</Pill>
            <Pill tone="muted">{rollup.mode}</Pill>
          </div>
        ) : null}
      </div>

      {interactions.length === 0 ? (
        <p className="text-sm text-[var(--color-muted)]">
          No events visible in your scope for this trace.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {interactions.map((event) => (
            <EventRow key={event.id} event={event} />
          ))}
        </ul>
      )}

      <p className="text-xs text-[var(--color-muted)]">
        Read-only · interaction trace (C8). Append-only audit; never mutated.
      </p>
    </div>
  );
}
