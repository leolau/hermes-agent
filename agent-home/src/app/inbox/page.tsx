import { MobileShell } from "@/components/MobileShell";
import { InboxView } from "@/components/inbox/InboxView";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { Change, Notification } from "@/types";

// Reads the live principal (cookie) + the caller's C2-scoped comms inbox per
// request — never at build time.
export const dynamic = "force-dynamic";

/**
 * FG-20 Wave C3 — the comms **Inbox** tab (FG-10 notifications + FG-12 change
 * undo/redo). BFF: the server resolves the principal and loads the C2-scoped
 * pending approvals/asks and the reversible change log from the Python API,
 * then hands them to the interactive {@link InboxView}. Answering an item and
 * undoing/redoing a change route back through `/api/comms/*` to the
 * principal-aware, cross-surface-deduped Python endpoints — the browser never
 * decides settlement or reversibility.
 */
export default async function Page() {
  await requirePrincipal();

  let configured = false;
  let notifications: Notification[] = [];
  let changes: Change[] = [];
  let error: string | null = null;
  try {
    const client = await apiClientForRequest();
    const [notifResp, changeResp] = await Promise.all([
      client.notifications(),
      client.changes(),
    ]);
    configured = notifResp.configured && changeResp.configured;
    notifications = notifResp.notifications;
    changes = changeResp.changes;
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load the inbox";
  }

  return (
    <MobileShell title="Inbox">
      {error ? (
        <div
          data-component="InboxError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load your inbox ({error}).
        </div>
      ) : (
        <InboxView
          initialConfigured={configured}
          initialNotifications={notifications}
          initialChanges={changes}
        />
      )}
    </MobileShell>
  );
}
