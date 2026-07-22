import { MobileShell } from "@/components/MobileShell";
import { WebviewConsole } from "@/components/webview/WebviewConsole";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { WebviewSession } from "@/types";

// Reads the live principal (cookie) + the caller's ephemeral webview session
// per request — never at build time.
export const dynamic = "force-dynamic";

/**
 * FG-20 Wave C2 — the agent-webview tab (FG-17b). BFF: the server resolves the
 * principal and loads the caller's open webview session (or the default-deny
 * empty state) from the Python API, then hands it to the interactive
 * {@link WebviewConsole}. Opening/closing sessions, requesting actions, and
 * resolving C6 approvals all route back through `/api/webview/*` to the
 * consent-gated (C6), per-user-isolated (C2), traced (C8) Python endpoints.
 */
export default async function Page() {
  await requirePrincipal();

  let configured = false;
  let session: WebviewSession | null = null;
  let error: string | null = null;
  try {
    const client = await apiClientForRequest();
    const resp = await client.getWebviewSession();
    configured = resp.configured;
    session = resp.session;
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load the webview";
  }

  return (
    <MobileShell title="Agent webview">
      {error ? (
        <div
          data-component="WebviewError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load the agent webview ({error}).
        </div>
      ) : (
        <WebviewConsole initialConfigured={configured} initialSession={session} />
      )}
    </MobileShell>
  );
}
