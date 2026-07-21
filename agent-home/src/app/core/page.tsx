import { CoreAreaView } from "@/components/core/CoreAreaView";
import { MobileShell } from "@/components/MobileShell";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { Change, CoreManifestResponse } from "@/types";

export const dynamic = "force-dynamic";

/**
 * FG-20 Wave B2 — Core-area view. BFF: resolves the principal and reads the
 * FG-14 Core-boundary projection (`/api/core/manifest`) plus the FG-12 change
 * log (`/api/comms/changes`, C2-scoped). Read-only: Core is immutable to the
 * runtime agent. Reached from Home (there is no bottom-nav slot for it).
 */
export default async function Page() {
  await requirePrincipal();

  let manifest: CoreManifestResponse | null = null;
  let changes: Change[] = [];
  let changesConfigured = false;
  let error: string | null = null;

  try {
    const client = await apiClientForRequest();
    manifest = await client.coreManifest(50);
    try {
      const changesResp = await client.changes();
      changesConfigured = changesResp.configured;
      changes = changesResp.changes ?? [];
    } catch {
      changesConfigured = false;
    }
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load the Core view";
  }

  return (
    <MobileShell title="Core area">
      {error ? (
        <div
          data-component="CoreError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load the Core boundary ({error}).
        </div>
      ) : (
        <CoreAreaView
          manifest={manifest}
          changes={changes}
          changesConfigured={changesConfigured}
        />
      )}
    </MobileShell>
  );
}
