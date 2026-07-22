import { Pill } from "@/components/ui/Pill";
import type { Change } from "@/types";

export interface ChangesListProps {
  changes: Change[];
  busyId: string | null;
  onOp: (change: Change, op: "undo" | "redo") => void;
  /**
   * Ids the upstream engine reported it cannot replay (a 409 on undo/redo).
   * The row is `reversible` in the log, but its inverse op was recorded by a
   * flow FG-12 does not know how to reverse, so we drop the button and show it
   * as review-only rather than offering an action that can never succeed.
   */
  blockedIds?: ReadonlySet<string>;
}

/**
 * FG-12 change log with undo/redo. An undone change offers **Redo**; a live,
 * reversible change offers **Undo**. Irreversible changes are shown for
 * review but expose no action (D6 is enforced upstream; the button is simply
 * absent). Visibility (C2) already scoped the list server-side.
 */
export function ChangesList({
  changes,
  busyId,
  onOp,
  blockedIds,
}: ChangesListProps) {
  if (changes.length === 0) {
    return (
      <section
        data-component="ChangesList"
        className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-6 text-center text-sm text-[var(--color-muted)]"
      >
        No changes visible in your scope.
      </section>
    );
  }

  return (
    <section data-component="ChangesList" className="flex flex-col gap-3">
      {changes.map((change) => {
        const busy = busyId === change.id;
        const blocked = blockedIds?.has(change.id) ?? false;
        return (
          <article
            key={change.id}
            className="flex flex-col gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Pill tone="muted">{change.target_kind}</Pill>
              {change.undone ? <Pill tone="warning">undone</Pill> : null}
              {!change.reversible ? (
                <Pill tone="danger">irreversible</Pill>
              ) : null}
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-[var(--color-muted)]">
                {change.id}
              </span>
            </div>
            <p className="text-xs text-[var(--color-muted)]">
              by {change.actor_user_id ?? "unknown"} · {change.mode}
            </p>

            {change.reversible && !blocked ? (
              change.undone ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onOp(change, "redo")}
                  className="min-h-11 w-fit rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                >
                  Redo
                </button>
              ) : (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onOp(change, "undo")}
                  className="min-h-11 w-fit rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm disabled:opacity-50"
                >
                  Undo
                </button>
              )
            ) : (
              <p className="text-xs text-[var(--color-muted)]">
                {blocked
                  ? "Not reversible here — review only."
                  : "Not reversible — review only."}
              </p>
            )}
          </article>
        );
      })}
    </section>
  );
}
