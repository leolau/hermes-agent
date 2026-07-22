"use client";

import { useState } from "react";

import { ApprovalsList } from "@/components/inbox/ApprovalsList";
import { ChangesList } from "@/components/inbox/ChangesList";
import { Pill } from "@/components/ui/Pill";
import type {
  Change,
  ChangeOpResponse,
  Notification,
  NotificationAnswerResponse,
  NotificationsResponse,
  ChangesResponse,
} from "@/types";

export interface InboxViewProps {
  initialConfigured: boolean;
  initialNotifications: Notification[];
  initialChanges: Change[];
}

type Tab = "approvals" | "changes";

async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const parsed = (await res.json()) as T & { detail?: string; error?: string };
  if (!res.ok) {
    throw new Error(parsed.detail ?? parsed.error ?? "The request was refused.");
  }
  return parsed;
}

/**
 * FG-20 Wave C3 — the mobile comms inbox. A segmented switch flips between the
 * FG-10 **Approvals** queue (grant/deny/acknowledge, deduped across surfaces)
 * and the FG-12 **Changes** log (undo/redo). Every mutation routes through the
 * `agent-home` BFF (`/api/comms/*`) under the C1 principal — this view never
 * decides settlement or reversibility, it forwards the user's intent and
 * re-reads the authoritative list afterwards.
 */
export function InboxView({
  initialConfigured,
  initialNotifications,
  initialChanges,
}: InboxViewProps) {
  const [configured] = useState(initialConfigured);
  const [tab, setTab] = useState<Tab>("approvals");
  const [notifications, setNotifications] = useState<Notification[]>(
    initialNotifications,
  );
  const [changes, setChanges] = useState<Change[]>(initialChanges);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function refreshNotifications() {
    try {
      const res = await fetch("/api/comms/notifications", { cache: "no-store" });
      if (!res.ok) return;
      const body = (await res.json()) as NotificationsResponse;
      setNotifications(body.notifications);
    } catch {
      // A stale list is non-fatal; the next action re-reads.
    }
  }

  async function refreshChanges() {
    try {
      const res = await fetch("/api/comms/changes", { cache: "no-store" });
      if (!res.ok) return;
      const body = (await res.json()) as ChangesResponse;
      setChanges(body.changes);
    } catch {
      // A stale list is non-fatal.
    }
  }

  async function answer(item: Notification, value: string) {
    setBusy(item.id);
    setError(null);
    setNotice(null);
    try {
      const resp = await postJson<NotificationAnswerResponse>(
        `/api/comms/notifications/${encodeURIComponent(item.id)}/answer`,
        { answer: value },
      );
      setNotice(
        resp.newly_answered
          ? `Answered "${item.title}" (${value}).`
          : `"${item.title}" was already answered on another surface.`,
      );
      await refreshNotifications();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The answer was refused.");
    } finally {
      setBusy(null);
    }
  }

  async function changeOp(change: Change, op: "undo" | "redo") {
    setBusy(change.id);
    setError(null);
    setNotice(null);
    try {
      const resp = await postJson<ChangeOpResponse>(
        `/api/comms/changes/${encodeURIComponent(change.id)}/${op}`,
      );
      setNotice(
        `${op === "undo" ? "Undid" : "Redid"} ${resp.target_kind} change.`,
      );
      await refreshChanges();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The change could not be applied.");
    } finally {
      setBusy(null);
    }
  }

  const pendingCount = notifications.filter((n) => n.status === "pending").length;
  const reversibleCount = changes.filter((c) => c.reversible).length;

  return (
    <div data-component="InboxView" className="flex flex-col gap-4">
      <p className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
        <Pill tone="accent">principal-scoped (C2)</Pill>
        <Pill tone="muted">deduped across surfaces</Pill>
      </p>

      <div
        role="tablist"
        aria-label="Inbox sections"
        className="grid grid-cols-2 gap-1 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-1"
      >
        <button
          type="button"
          role="tab"
          aria-selected={tab === "approvals"}
          onClick={() => setTab("approvals")}
          className={`flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm ${
            tab === "approvals"
              ? "bg-[var(--color-accent)] text-[var(--color-accent-fg)]"
              : "text-[var(--color-muted)]"
          }`}
        >
          Approvals
          {pendingCount > 0 ? (
            <span className="rounded-full bg-[var(--color-surface-2)] px-2 text-xs text-[var(--color-fg)]">
              {pendingCount}
            </span>
          ) : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "changes"}
          onClick={() => setTab("changes")}
          className={`flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm ${
            tab === "changes"
              ? "bg-[var(--color-accent)] text-[var(--color-accent-fg)]"
              : "text-[var(--color-muted)]"
          }`}
        >
          Changes
          {reversibleCount > 0 ? (
            <span className="rounded-full bg-[var(--color-surface-2)] px-2 text-xs text-[var(--color-fg)]">
              {reversibleCount}
            </span>
          ) : null}
        </button>
      </div>

      {!configured ? (
        <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]">
          The comms inbox needs the multi-user datastore configured.
        </div>
      ) : null}

      {notice ? (
        <p className="rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm text-[var(--color-fg)]">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p className="rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm text-red-300">
          {error}
        </p>
      ) : null}

      {tab === "approvals" ? (
        <ApprovalsList
          notifications={notifications}
          busyId={busy}
          onAnswer={answer}
        />
      ) : (
        <ChangesList changes={changes} busyId={busy} onOp={changeOp} />
      )}
    </div>
  );
}
