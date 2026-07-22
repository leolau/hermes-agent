"use client";

import { Pill } from "@/components/ui/Pill";
import type { WebviewPendingApproval } from "@/types";

export interface PendingApprovalsProps {
  pending: WebviewPendingApproval[];
  busy: boolean;
  onResolve: (approvalId: string, grant: boolean) => void | Promise<void>;
}

/**
 * The per-action C6 approval queue (FG-17b). Escalated actions (off-scope,
 * interactive-under-read-only, credentialed, or destructive) wait here until the
 * user grants or denies them; on grant the action runs and is traced (C8).
 */
export function PendingApprovals({ pending, busy, onResolve }: PendingApprovalsProps) {
  return (
    <section data-component="PendingApprovals" className="flex flex-col gap-3">
      <span className="text-sm font-semibold">Pending approvals (C6)</span>
      {pending.length === 0 ? (
        <p className="text-sm text-[var(--color-muted)]">
          No actions are waiting for approval.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {pending.map((p) => (
            <li
              key={p.id}
              className="flex flex-col gap-2 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Pill tone="warning">{p.kind}</Pill>
                {p.url ? (
                  <code className="truncate text-xs text-[var(--color-muted)]">
                    {p.url}
                  </code>
                ) : null}
              </div>
              <span className="text-sm text-[var(--color-muted)]">{p.reason}</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onResolve(p.id, true)}
                  className="rounded-xl bg-[var(--color-accent)] px-3 py-2 text-sm font-semibold text-[var(--color-accent-fg)] disabled:opacity-50"
                >
                  Approve
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onResolve(p.id, false)}
                  className="rounded-xl border border-[var(--color-border)] px-3 py-2 text-sm disabled:opacity-50"
                >
                  Deny
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
