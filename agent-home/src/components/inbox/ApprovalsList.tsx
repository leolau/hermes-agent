import { Pill } from "@/components/ui/Pill";
import type { Notification } from "@/types";

export interface ApprovalsListProps {
  notifications: Notification[];
  busyId: string | null;
  onAnswer: (item: Notification, value: string) => void;
}

/**
 * FG-10 approvals + proactive asks. Approvals expose Approve/Deny; asks expose
 * Acknowledge. Settled items are shown greyed with their answer so the mobile
 * surface matches the operator console's cross-surface state.
 */
export function ApprovalsList({
  notifications,
  busyId,
  onAnswer,
}: ApprovalsListProps) {
  if (notifications.length === 0) {
    return (
      <section
        data-component="ApprovalsList"
        className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-6 text-center text-sm text-[var(--color-muted)]"
      >
        No pending approvals or asks in your scope.
      </section>
    );
  }

  return (
    <section data-component="ApprovalsList" className="flex flex-col gap-3">
      {notifications.map((item) => {
        const pending = item.status === "pending";
        const busy = busyId === item.id;
        return (
          <article
            key={item.id}
            className="flex flex-col gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Pill tone={item.kind === "approval" ? "accent" : "muted"}>
                {item.kind}
              </Pill>
              {!item.reversible ? <Pill tone="danger">irreversible</Pill> : null}
              {!pending ? <Pill tone="muted">{item.status}</Pill> : null}
              <span className="min-w-0 flex-1 truncate text-sm font-semibold">
                {item.title}
              </span>
            </div>
            {item.body ? (
              <p className="text-sm text-[var(--color-muted)]">{item.body}</p>
            ) : null}
            {item.command ? (
              <code className="block overflow-x-auto rounded-md bg-[var(--color-surface-2)] px-2 py-1 font-mono text-xs">
                {item.command}
              </code>
            ) : null}

            {pending ? (
              <div className="flex flex-wrap gap-2">
                {item.kind === "approval" ? (
                  <>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onAnswer(item, "approved")}
                      className="min-h-11 flex-1 rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onAnswer(item, "denied")}
                      className="min-h-11 flex-1 rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm text-red-300 disabled:opacity-50"
                    >
                      Deny
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onAnswer(item, "acknowledged")}
                    className="min-h-11 flex-1 rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                  >
                    Acknowledge
                  </button>
                )}
              </div>
            ) : (
              <p className="text-xs text-[var(--color-muted)]">
                {item.answer ? `Answered "${item.answer}"` : "Settled"}
                {item.answered_via ? ` via ${item.answered_via}` : ""}
                {item.answered_by ? ` by ${item.answered_by}` : ""}.
              </p>
            )}
          </article>
        );
      })}
    </section>
  );
}
