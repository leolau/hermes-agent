import type { SessionSummary } from "@/types";

function relative(ts: number | null): string {
  if (!ts) return "";
  const secs = Math.max(0, Date.now() / 1000 - ts);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

/**
 * The conversation switcher, shown as a bottom sheet on mobile. Lists the
 * principal's `agent_home` conversations (most-recent first) with a preview and
 * relative timestamp; tapping one opens its transcript.
 */
export function ConversationList({
  sessions,
  activeId,
  onSelect,
  onClose,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <div
      data-component="ConversationList"
      className="fixed inset-0 z-40 flex flex-col justify-end bg-black/50"
      onClick={onClose}
    >
      <div
        className="max-h-[70dvh] overflow-y-auto rounded-t-2xl border-t border-[var(--color-border)] bg-[var(--color-bg)] p-4"
        style={{ paddingBottom: "calc(var(--safe-bottom) + 1rem)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Conversations</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-sm text-[var(--color-muted)]"
          >
            Close
          </button>
        </div>
        {sessions.length === 0 ? (
          <p className="py-6 text-center text-sm text-[var(--color-muted)]">
            No conversations yet.
          </p>
        ) : (
          <ul className="space-y-2">
            {sessions.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => onSelect(s.id)}
                  className={`w-full rounded-xl border px-3 py-2 text-left ${
                    s.id === activeId
                      ? "border-[var(--color-accent)] bg-[var(--color-surface-2)]"
                      : "border-[var(--color-border)] bg-[var(--color-surface)]"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">
                      {s.title || s.preview || "Untitled"}
                    </span>
                    <span className="shrink-0 text-xs text-[var(--color-muted)]">
                      {relative(s.last_active)}
                    </span>
                  </div>
                  {s.preview ? (
                    <p className="mt-0.5 truncate text-xs text-[var(--color-muted)]">
                      {s.preview}
                    </p>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
