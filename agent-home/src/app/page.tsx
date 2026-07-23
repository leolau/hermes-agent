import Link from "next/link";

import { LogoutButton } from "@/components/LogoutButton";
import { MobileShell } from "@/components/MobileShell";
import { requirePrincipal } from "@/lib/auth/principal";
import { scopedSelect } from "@/lib/supabase/context";

// The seam proof reads the live principal (cookie) + Supabase, so it must
// render per-request, never at build time.
export const dynamic = "force-dynamic";

interface ScopedRow extends Record<string, unknown> {
  id: string;
  title?: string | null;
  visibility?: string | null;
}

/**
 * Wave-A seam proof (NOT a feature panel): shows the logged-in C1 principal
 * and one RLS-scoped read from Supabase, proving the bridge end-to-end
 * (principal → `hermes.principal_*` GUCs → FORCE'd RLS). Wave B/C replace this
 * with the real GTS/chat panels.
 */
export default async function HomePage() {
  const principal = await requirePrincipal();

  let rows: ScopedRow[] = [];
  let readError: string | null = null;
  try {
    rows = await scopedSelect<ScopedRow>(principal, "goals", {
      columns: "id, title, visibility",
      limit: 5,
    });
  } catch (err) {
    readError = err instanceof Error ? err.message : "scoped read failed";
  }

  return (
    <MobileShell title="Agent Home">
      <section
        data-component="PrincipalCard"
        className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
      >
        <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
          Signed in as
        </p>
        <p className="mt-1 text-lg font-semibold">{principal.display}</p>
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-[var(--color-surface-2)] px-2 py-1">
            {principal.user_id}
          </span>
          <span className="rounded-full bg-[var(--color-accent)] px-2 py-1 text-[var(--color-accent-fg)]">
            {principal.role}
          </span>
          {principal.is_owner ? (
            <span className="rounded-full bg-[var(--color-surface-2)] px-2 py-1">
              owner
            </span>
          ) : null}
        </div>
      </section>

      <section
        data-component="ScopedReadCard"
        className="mt-4 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
      >
        <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
          RLS-scoped read · goals
        </p>
        {readError ? (
          <p className="mt-2 text-sm text-[var(--color-muted)]">
            Seam wired; live read unavailable here ({readError}).
          </p>
        ) : rows.length === 0 ? (
          <p className="mt-2 text-sm text-[var(--color-muted)]">
            No rows visible to this principal (RLS returned an empty set).
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {rows.map((row) => (
              <li
                key={row.id}
                className="rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm"
              >
                <span className="block truncate">{row.title ?? row.id}</span>
                <span className="text-xs text-[var(--color-muted)]">
                  {row.visibility ?? "—"}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-[var(--color-muted)]">
          Rows are filtered by Postgres FORCE&apos;d RLS using the bound
          <code className="mx-1">hermes.principal_*</code>GUCs — the browser
          never sees rows it may not.
        </p>
      </section>

      <nav
        data-component="HomeLinks"
        className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2"
      >
        {principal.role === "owner" || principal.role === "admin" ? (
          <Link
            href="/members"
            data-component="MembersLink"
            className="flex items-center justify-between rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 active:opacity-70"
          >
            <span>
              <span className="block text-sm font-medium">Members</span>
              <span className="block text-xs text-[var(--color-muted)]">
                Create &amp; manage members (owner/admin)
              </span>
            </span>
            <span aria-hidden className="text-[var(--color-muted)]">
              ›
            </span>
          </Link>
        ) : null}

        <Link
          href="/onboarding"
          data-component="OnboardingLink"
          className="flex items-center justify-between rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 active:opacity-70"
        >
          <span>
            <span className="block text-sm font-medium">Getting started</span>
            <span className="block text-xs text-[var(--color-muted)]">
              FG-15 setup readiness (read-only)
            </span>
          </span>
          <span aria-hidden className="text-[var(--color-muted)]">
            ›
          </span>
        </Link>

        <Link
          href="/tools"
          data-component="ToolsLink"
          className="flex items-center justify-between rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 active:opacity-70"
        >
          <span>
            <span className="block text-sm font-medium">Tools</span>
            <span className="block text-xs text-[var(--color-muted)]">
              FG-07 tool registry (read-only)
            </span>
          </span>
          <span aria-hidden className="text-[var(--color-muted)]">
            ›
          </span>
        </Link>

        <Link
          href="/core"
          data-component="CoreAreaLink"
          className="flex items-center justify-between rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 active:opacity-70"
        >
          <span>
            <span className="block text-sm font-medium">Core area</span>
            <span className="block text-xs text-[var(--color-muted)]">
              C7 boundary · change log · denials (read-only)
            </span>
          </span>
          <span aria-hidden className="text-[var(--color-muted)]">
            ›
          </span>
        </Link>

        <Link
          href="/webview"
          data-component="WebviewLink"
          className="flex items-center justify-between rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 active:opacity-70"
        >
          <span>
            <span className="block text-sm font-medium">Agent webview</span>
            <span className="block text-xs text-[var(--color-muted)]">
              FG-17b CDP browser · consent-gated (C6) · traced (C8)
            </span>
          </span>
          <span aria-hidden className="text-[var(--color-muted)]">
            ›
          </span>
        </Link>
      </nav>

      <LogoutButton />
    </MobileShell>
  );
}
