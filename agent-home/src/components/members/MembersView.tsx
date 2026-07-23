"use client";

import { type FormEvent, useState } from "react";

import { Pill } from "@/components/ui/Pill";
import type {
  Member,
  MemberCreateResponse,
  MembersResponse,
  Role,
} from "@/types";

export interface MembersViewProps {
  initialConfigured: boolean;
  initialMembers: Member[];
}

/** Roles a member can be assigned here — never `owner` (that's `hermes owner`). */
const ASSIGNABLE_ROLES: readonly Role[] = ["admin", "member", "viewer"] as const;

class ForwardError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ForwardError";
    this.status = status;
  }
}

async function sendJson<T>(
  url: string,
  method: "POST" | "PUT",
  body?: unknown,
): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const parsed = (await res.json()) as T & { detail?: string; error?: string };
  if (!res.ok) {
    throw new ForwardError(
      res.status,
      parsed.detail ?? parsed.error ?? "The request was refused.",
    );
  }
  return parsed;
}

/** A URL-safe temporary password the owner can hand over (client-generated). */
function generatePassword(): string {
  const bytes = new Uint8Array(18);
  crypto.getRandomValues(bytes);
  let out = "";
  for (const b of bytes) out += (b % 36).toString(36);
  return `Hz-${out}`;
}

/**
 * FG-20 PR-4 — the owner/admin **Members** screen. Create members (a Supabase
 * account is minted and enrolled as a principal server-side), change roles,
 * reset temporary passwords, and deactivate/reactivate login. Every mutation
 * routes through the owner/admin-guarded `/api/comms/members` BFF — this view
 * never calls GoTrue and never holds the service-role key. The owner row is
 * read-only here (ownership moves via `hermes owner transfer`).
 */
export function MembersView({
  initialConfigured,
  initialMembers,
}: MembersViewProps) {
  const [configured] = useState(initialConfigured);
  const [members, setMembers] = useState<Member[]>(initialMembers);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Add-member form.
  const [email, setEmail] = useState("");
  const [display, setDisplay] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [password, setPassword] = useState("");
  // A temp password to surface exactly once after create/reset — never fetched
  // back from the server, only echoed from what this browser just set.
  const [revealed, setRevealed] = useState<{ label: string; password: string } | null>(
    null,
  );

  async function refresh() {
    try {
      const res = await fetch("/api/comms/members", { cache: "no-store" });
      if (!res.ok) return;
      const body = (await res.json()) as MembersResponse;
      setMembers(body.members);
    } catch {
      // A stale list is non-fatal; the next action re-reads.
    }
  }

  function reset(message: string | null) {
    setError(null);
    setNotice(message);
  }

  async function createMember(evt: FormEvent) {
    evt.preventDefault();
    const pw = password || generatePassword();
    setBusy("create");
    reset(null);
    try {
      const resp = await sendJson<MemberCreateResponse>(
        "/api/comms/members",
        "POST",
        { email, display, role, password: pw },
      );
      setRevealed({ label: email, password: pw });
      setNotice(`Created ${resp.member.user_id} as ${resp.member.role}.`);
      setEmail("");
      setDisplay("");
      setRole("member");
      setPassword("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the member.");
    } finally {
      setBusy(null);
    }
  }

  async function changeRole(member: Member, next: Role) {
    setBusy(member.user_id);
    reset(null);
    try {
      await sendJson("/api/comms/members/" + encodeURIComponent(member.user_id) + "/role", "PUT", {
        role: next,
      });
      setNotice(`${member.display || member.user_id} is now ${next}.`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the role.");
    } finally {
      setBusy(null);
    }
  }

  async function resetPassword(member: Member) {
    const pw = generatePassword();
    setBusy(member.user_id);
    reset(null);
    try {
      await sendJson(
        "/api/comms/members/" + encodeURIComponent(member.user_id) + "/password",
        "POST",
        { password: pw },
      );
      setRevealed({ label: member.email || member.user_id, password: pw });
      setNotice(`Reset the password for ${member.display || member.user_id}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset the password.");
    } finally {
      setBusy(null);
    }
  }

  async function setActive(member: Member, active: boolean) {
    setBusy(member.user_id);
    reset(null);
    try {
      await sendJson(
        "/api/comms/members/" +
          encodeURIComponent(member.user_id) +
          (active ? "/activate" : "/deactivate"),
        "POST",
      );
      setNotice(
        `${member.display || member.user_id} ${active ? "reactivated" : "deactivated"}.`,
      );
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the member.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div data-component="MembersView" className="flex flex-col gap-4">
      <p className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
        <Pill tone="accent">owner/admin only</Pill>
        <Pill tone="muted">Supabase accounts</Pill>
      </p>

      {!configured ? (
        <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]">
          Member management isn&apos;t configured on this server yet (it needs
          the Supabase GoTrue URL + service-role key set server-side).
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

      {revealed ? (
        <div
          data-component="RevealedPassword"
          className="rounded-2xl border border-[var(--color-accent)] bg-[var(--color-surface)] p-4"
        >
          <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
            Temporary password for {revealed.label}
          </p>
          <p className="mt-1 break-all font-mono text-sm">{revealed.password}</p>
          <p className="mt-2 text-xs text-[var(--color-muted)]">
            Share it securely — it won&apos;t be shown again. The member should
            change it after signing in.
          </p>
          <button
            type="button"
            onClick={() => setRevealed(null)}
            className="mt-3 rounded-lg bg-[var(--color-surface-2)] px-3 py-1 text-xs"
          >
            Done
          </button>
        </div>
      ) : null}

      <form
        data-component="AddMemberForm"
        onSubmit={createMember}
        className="flex flex-col gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
      >
        <p className="text-sm font-medium">Add a member</p>
        <input
          type="email"
          required
          placeholder="email@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm"
        />
        <input
          type="text"
          placeholder="Display name (optional)"
          value={display}
          onChange={(e) => setDisplay(e.target.value)}
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm"
        />
        <label className="flex items-center justify-between gap-2 text-sm">
          <span className="text-[var(--color-muted)]">Role</span>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm"
          >
            {ASSIGNABLE_ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Temporary password (blank = generate)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 font-mono text-sm"
          />
          <button
            type="button"
            onClick={() => setPassword(generatePassword())}
            className="shrink-0 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-xs"
          >
            Generate
          </button>
        </div>
        <button
          type="submit"
          disabled={busy === "create" || !configured}
          className="rounded-lg bg-[var(--color-accent)] px-3 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
        >
          {busy === "create" ? "Creating…" : "Create member"}
        </button>
      </form>

      <ul data-component="MembersList" className="flex flex-col gap-2">
        {members.map((member) => (
          <MemberRow
            key={member.user_id}
            member={member}
            busy={busy === member.user_id}
            onChangeRole={changeRole}
            onResetPassword={resetPassword}
            onSetActive={setActive}
          />
        ))}
        {members.length === 0 ? (
          <li className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]">
            No members enrolled yet.
          </li>
        ) : null}
      </ul>
    </div>
  );
}

function MemberRow({
  member,
  busy,
  onChangeRole,
  onResetPassword,
  onSetActive,
}: {
  member: Member;
  busy: boolean;
  onChangeRole: (m: Member, r: Role) => void;
  onResetPassword: (m: Member) => void;
  onSetActive: (m: Member, active: boolean) => void;
}) {
  return (
    <li
      data-component="MemberRow"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            {member.display || member.email || member.user_id}
          </p>
          <p className="truncate text-xs text-[var(--color-muted)]">
            {member.email || "(no email)"} · {member.user_id}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-[var(--color-accent)] px-2 py-1 text-[var(--color-accent-fg)]">
              {member.role}
            </span>
            {member.active ? null : (
              <span className="rounded-full bg-[var(--color-surface-2)] px-2 py-1 text-red-300">
                deactivated
              </span>
            )}
            {member.channels.map((c) => (
              <span key={c} className="rounded-full bg-[var(--color-surface-2)] px-2 py-1">
                {c}
              </span>
            ))}
          </div>
        </div>
      </div>

      {member.is_owner ? (
        <p className="mt-3 text-xs text-[var(--color-muted)]">
          Owner — managed via <code>hermes owner transfer</code>.
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-[var(--color-muted)]">
            Role
            <select
              value={member.role}
              disabled={busy}
              onChange={(e) => onChangeRole(member, e.target.value as Role)}
              className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-xs disabled:opacity-50"
            >
              {ASSIGNABLE_ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() => onResetPassword(member)}
            className="rounded-lg bg-[var(--color-surface-2)] px-3 py-1 text-xs disabled:opacity-50"
          >
            Reset password
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onSetActive(member, !member.active)}
            className="rounded-lg bg-[var(--color-surface-2)] px-3 py-1 text-xs disabled:opacity-50"
          >
            {member.active ? "Deactivate" : "Reactivate"}
          </button>
        </div>
      )}
    </li>
  );
}
