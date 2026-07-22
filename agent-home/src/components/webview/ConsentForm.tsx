"use client";

import { useState } from "react";

import { parseAllowedDomains } from "@/lib/webview";
import type { WebviewMode } from "@/types";

export interface ConsentFormProps {
  busy: boolean;
  onOpen: (allowedDomains: string[], mode: WebviewMode) => void | Promise<void>;
}

/**
 * The default-deny opt-in card (FG-17b / C6). Nothing runs until the user opens
 * a session with an explicit consent scope: a set of allowed domains and
 * whether interactive actions (click/type/select) may run autonomously in scope.
 */
export function ConsentForm({ busy, onOpen }: ConsentFormProps) {
  const [domains, setDomains] = useState("");
  const [interactive, setInteractive] = useState(false);

  return (
    <section
      data-component="ConsentForm"
      className="flex flex-col gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <div className="flex items-center gap-2">
        <span aria-hidden className="text-lg">
          🔒
        </span>
        <span className="text-sm font-semibold">No session (default-deny)</span>
      </div>
      <p className="text-sm text-[var(--color-muted)]">
        The agent cannot drive the browser until you opt in with a consent scope.
        In-scope reads run autonomously; everything else escalates for your
        approval.
      </p>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-[var(--color-muted)]">
          Allowed domains (comma-separated)
        </span>
        <input
          type="text"
          inputMode="url"
          placeholder="example.com, docs.internal"
          value={domains}
          onChange={(e) => setDomains(e.target.value)}
          className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm"
        />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={interactive}
          onChange={(e) => setInteractive(e.target.checked)}
          className="h-5 w-5 accent-[var(--color-accent)]"
        />
        Allow interactive actions (click / type / select) in scope
      </label>
      <button
        type="button"
        disabled={busy}
        onClick={() =>
          void onOpen(
            parseAllowedDomains(domains),
            interactive ? "interactive" : "read_only",
          )
        }
        className="w-fit rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-[var(--color-accent-fg)] disabled:opacity-50"
      >
        Open session
      </button>
    </section>
  );
}
