import type { ReactNode } from "react";

import { Pill } from "@/components/ui/Pill";
import type { Change, CoreManifestResponse } from "@/types";

/**
 * FG-20 Wave B2 — mobile-first Core-area view (read-only, C7).
 *
 * The permanent window on the FG-14 Core boundary the runtime agent may never
 * cross: the active `core_manifest.yaml` globs, boundary health
 * (`fallback_active` → running on the baked-in fail-closed set), the durable
 * Core-write denial audit tail, and the FG-12 change log. It is the mobile face
 * of `web/`'s `CorePage`; Core is immutable to the runtime agent and changes
 * only through the human repo/PR flow, so nothing here mutates state.
 */

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section data-component="CoreSection" className="mt-4 first:mt-0">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Card({ children }: { children: ReactNode }) {
  return (
    <div
      data-component="CoreCard"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      {children}
    </div>
  );
}

function BoundaryHealth({ manifest }: { manifest: CoreManifestResponse }) {
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Core boundary</span>
        <Pill tone={manifest.fallback_active ? "warning" : "success"}>
          {manifest.fallback_active ? "fail-closed fallback" : "manifest active"}
        </Pill>
        {manifest.self_protected ? <Pill tone="muted">self-protected</Pill> : null}
      </div>
      <p className="mt-2 text-sm text-[var(--color-muted)]">
        <code>{manifest.manifest_path}</code> defines{" "}
        <strong>{manifest.glob_count}</strong> Core globs. The runtime agent can
        never write these paths; Core changes only through the human repo/PR flow.
      </p>
      {manifest.globs.length > 0 ? (
        <ul className="mt-3 grid grid-cols-1 gap-1">
          {manifest.globs.map((glob) => (
            <li
              key={glob}
              className="rounded-lg bg-[var(--color-surface-2)] px-2 py-1 font-mono text-xs"
            >
              {glob}
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

export function CoreAreaView({
  manifest,
  changes,
  changesConfigured,
}: {
  manifest: CoreManifestResponse | null;
  changes: Change[];
  changesConfigured: boolean;
}) {
  return (
    <div data-component="CoreAreaView" className="flex flex-col">
      <Section title="Boundary health">
        {manifest ? (
          <BoundaryHealth manifest={manifest} />
        ) : (
          <p className="text-sm text-[var(--color-muted)]">
            Core boundary unavailable.
          </p>
        )}
      </Section>

      <Section title="Boundary denials">
        {manifest && manifest.denials.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {manifest.denials.map((denial) => (
              <li key={denial.id}>
                <Card>
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone="warning">{denial.mode}</Pill>
                    <span className="text-xs text-[var(--color-muted)]">
                      {denial.actor_user_id}
                    </span>
                  </div>
                  <p className="mt-1 text-sm">{denial.summary}</p>
                </Card>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">
            No Core-write denials recorded — the agent has not attempted to cross
            the boundary.
          </p>
        )}
      </Section>

      <Section title="Change log (FG-12)">
        {!changesConfigured ? (
          <p className="text-sm text-[var(--color-muted)]">
            Change log not configured (needs the application datastore).
          </p>
        ) : changes.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            No changes visible in your scope.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {changes.map((change) => (
              <li key={change.id}>
                <Card>
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone="muted">{change.mode}</Pill>
                    <Pill tone="muted">{change.target_kind}</Pill>
                    {change.reversible ? <Pill tone="muted">reversible</Pill> : null}
                    {change.undone ? <Pill tone="warning">undone</Pill> : null}
                    <span className="text-xs text-[var(--color-muted)]">
                      {change.actor_user_id ?? "—"}
                    </span>
                  </div>
                  <code className="mt-1 block text-xs text-[var(--color-muted)]">
                    {change.id.slice(0, 24)}
                  </code>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <p className="mt-4 text-xs text-[var(--color-muted)]">
        Read-only · Core (C7). The boundary is exposed here, never mutated.
      </p>
    </div>
  );
}
