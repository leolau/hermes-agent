import { MobileShell } from "@/components/MobileShell";
import { MembersView } from "@/components/members/MembersView";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { Member } from "@/types";

// Reads the live principal (cookie) + the owner/admin member roster per
// request — never at build time.
export const dynamic = "force-dynamic";

/**
 * FG-20 PR-4 — the owner/admin **Members** screen (multi-user item e). The
 * server resolves the principal and hard-gates on owner/admin (a member/viewer
 * gets a not-authorized card, never the roster). For an authorised actor it
 * loads the member list from the Python API and hands it to the interactive
 * {@link MembersView}; every mutation routes back through the owner/admin-guarded
 * `/api/comms/members` BFF. The browser never calls GoTrue.
 */
export default async function Page() {
  const principal = await requirePrincipal();

  if (principal.role !== "owner" && principal.role !== "admin") {
    return (
      <MobileShell title="Members">
        <div
          data-component="MembersForbidden"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Only the owner or an admin can manage members.
        </div>
      </MobileShell>
    );
  }

  let configured = false;
  let members: Member[] = [];
  let error: string | null = null;
  try {
    const client = await apiClientForRequest();
    const resp = await client.members();
    configured = resp.configured;
    members = resp.members;
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load members";
  }

  return (
    <MobileShell title="Members">
      {error ? (
        <div
          data-component="MembersError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load members ({error}).
        </div>
      ) : (
        <MembersView initialConfigured={configured} initialMembers={members} />
      )}
    </MobileShell>
  );
}
