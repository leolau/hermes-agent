"use client";

import { useState } from "react";

import { Pill } from "@/components/ui/Pill";
import { decisionTone } from "@/lib/webview";
import type { WebviewActionKind, WebviewActionResponse } from "@/types";

const ACTION_KINDS: WebviewActionKind[] = [
  "navigate",
  "read",
  "screenshot",
  "scroll",
  "click",
  "type",
  "select",
  "submit",
  "download",
];

export interface ActionConsoleProps {
  busy: boolean;
  lastResult: WebviewActionResponse | null;
  onRequest: (input: {
    kind: WebviewActionKind;
    url: string;
    credentialed: boolean;
    destructive: boolean;
  }) => void | Promise<void>;
}

/**
 * Request one agent action against the live page. The server's Option-B policy
 * decides allow vs escalate; this console only submits the request and shows the
 * returned decision + reason (never deciding consent itself).
 */
export function ActionConsole({ busy, lastResult, onRequest }: ActionConsoleProps) {
  const [kind, setKind] = useState<WebviewActionKind>("navigate");
  const [url, setUrl] = useState("");
  const [credentialed, setCredentialed] = useState(false);
  const [destructive, setDestructive] = useState(false);

  return (
    <section
      data-component="ActionConsole"
      className="flex flex-col gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <span className="text-sm font-semibold">Request an action</span>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-[var(--color-muted)]">Kind</span>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as WebviewActionKind)}
          className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm"
        >
          {ACTION_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-[var(--color-muted)]">URL (optional)</span>
        <input
          type="text"
          inputMode="url"
          placeholder="https://example.com/page"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm"
        />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={credentialed}
          onChange={(e) => setCredentialed(e.target.checked)}
          className="h-5 w-5 accent-[var(--color-accent)]"
        />
        Credentialed (login / secret entry)
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={destructive}
          onChange={(e) => setDestructive(e.target.checked)}
          className="h-5 w-5 accent-[var(--color-accent)]"
        />
        Destructive (purchase / delete / submit)
      </label>
      <button
        type="button"
        disabled={busy}
        onClick={() => void onRequest({ kind, url, credentialed, destructive })}
        className="w-fit rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-[var(--color-accent-fg)] disabled:opacity-50"
      >
        Request action
      </button>

      {lastResult ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Pill tone={decisionTone(lastResult.decision)}>{lastResult.decision}</Pill>
          <span className="text-[var(--color-muted)]">
            {lastResult.reason}
            {lastResult.detail ? ` — ${lastResult.detail}` : ""}
          </span>
        </div>
      ) : null}
    </section>
  );
}
