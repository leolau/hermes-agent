"use client";

import { useState } from "react";

import { ActionConsole } from "@/components/webview/ActionConsole";
import { ConsentForm } from "@/components/webview/ConsentForm";
import { PendingApprovals } from "@/components/webview/PendingApprovals";
import { Pill } from "@/components/ui/Pill";
import type {
  WebviewActionKind,
  WebviewActionResponse,
  WebviewMode,
  WebviewSession,
} from "@/types";

export interface WebviewConsoleProps {
  initialConfigured: boolean;
  initialSession: WebviewSession | null;
}

interface ActionInput {
  kind: WebviewActionKind;
  url: string;
  credentialed: boolean;
  destructive: boolean;
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const parsed = (await res.json()) as T & { detail?: string; error?: string };
  if (!res.ok) {
    throw new Error(parsed.detail ?? parsed.error ?? "The request was refused.");
  }
  return parsed;
}

/**
 * FG-20 Wave C2 — the mobile-first agent-webview console (FG-17b). Default-deny:
 * with no open session the agent cannot touch the browser. Opening a session
 * grants a consent scope (allowed domains + read-only/interactive); in-scope
 * reads run autonomously, everything else escalates to a per-action C6 approval.
 * Every decision is traced (C8). This console drives the policy through the
 * `agent-home` BFF (`/api/webview/*`) — it never bypasses or re-implements it.
 */
export function WebviewConsole({
  initialConfigured,
  initialSession,
}: WebviewConsoleProps) {
  const [configured] = useState(initialConfigured);
  const [session, setSession] = useState<WebviewSession | null>(initialSession);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<WebviewActionResponse | null>(null);

  async function openSession(allowed_domains: string[], mode: WebviewMode) {
    setBusy(true);
    setError(null);
    try {
      const resp = await postJson<{ session: WebviewSession | null }>(
        "/api/webview/session",
        { allowed_domains, mode },
      );
      setSession(resp.session);
      setLastResult(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open session.");
    } finally {
      setBusy(false);
    }
  }

  async function closeSession() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/webview/session", {
        method: "DELETE",
        cache: "no-store",
      });
      if (!res.ok) {
        const body = (await res.json()) as { detail?: string };
        throw new Error(body.detail ?? "Failed to close session.");
      }
      setSession(null);
      setLastResult(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to close session.");
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    try {
      const res = await fetch("/api/webview/session", { cache: "no-store" });
      if (!res.ok) return;
      const body = (await res.json()) as { session: WebviewSession | null };
      setSession(body.session);
    } catch {
      // A stale session view is non-fatal.
    }
  }

  async function requestAction(input: ActionInput) {
    setBusy(true);
    setError(null);
    try {
      const resp = await postJson<WebviewActionResponse>("/api/webview/action", {
        kind: input.kind,
        url: input.url.trim() || null,
        credentialed: input.credentialed,
        destructive: input.destructive,
      });
      setLastResult(resp);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The action was refused.");
    } finally {
      setBusy(false);
    }
  }

  async function resolveApproval(approvalId: string, grant: boolean) {
    setBusy(true);
    setError(null);
    try {
      const resp = await postJson<WebviewActionResponse>(
        `/api/webview/approval/${encodeURIComponent(approvalId)}`,
        { grant },
      );
      setLastResult(resp);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resolve approval.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-component="WebviewConsole" className="flex flex-col gap-4">
      <p className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
        <Pill tone="accent">consent-gated (C6)</Pill>
        <Pill tone="muted">traced (C8)</Pill>
        <span>Per-user browser profile · isolated (C2).</span>
      </p>

      {!configured ? (
        <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]">
          Webview needs the multi-user datastore configured.
        </div>
      ) : null}

      {error ? (
        <p className="rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm text-red-300">
          {error}
        </p>
      ) : null}

      {session === null ? (
        <ConsentForm busy={busy} onOpen={openSession} />
      ) : (
        <>
          <section
            data-component="WebviewSessionCard"
            className="flex flex-col gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold">Session open</span>
              <Pill tone={session.scope.mode === "interactive" ? "warning" : "success"}>
                {session.scope.mode}
              </Pill>
              <Pill tone="muted">
                {session.scope.allowed_domains.length} domain
                {session.scope.allowed_domains.length === 1 ? "" : "s"}
              </Pill>
            </div>
            <code className="text-xs text-[var(--color-muted)]">
              trace {session.trace_id.slice(0, 20)}
            </code>
            <ul className="flex flex-wrap gap-1">
              {session.scope.allowed_domains.length === 0 ? (
                <li className="text-xs text-[var(--color-muted)]">
                  No domains in scope — every navigation will escalate.
                </li>
              ) : (
                session.scope.allowed_domains.map((d) => (
                  <li
                    key={d}
                    className="rounded-md bg-[var(--color-surface-2)] px-2 py-1 font-mono text-xs"
                  >
                    {d}
                  </li>
                ))
              )}
            </ul>
            <button
              type="button"
              disabled={busy}
              onClick={() => void closeSession()}
              className="w-fit rounded-xl border border-[var(--color-border)] px-3 py-2 text-sm disabled:opacity-50"
            >
              Close session
            </button>
          </section>

          <ActionConsole
            busy={busy}
            lastResult={lastResult}
            onRequest={requestAction}
          />

          <PendingApprovals
            pending={session.pending}
            busy={busy}
            onResolve={resolveApproval}
          />
        </>
      )}
    </div>
  );
}
