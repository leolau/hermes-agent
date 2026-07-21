"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/** Clears the `agent-home` session then returns to the login page. */
export function LogoutButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button
      data-component="LogoutButton"
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await fetch("/api/session/logout", { method: "POST" });
        } finally {
          router.replace("/login");
          router.refresh();
        }
      }}
      className="mt-6 block w-full rounded-xl border border-[var(--color-border)] px-4 py-3 text-center text-sm text-[var(--color-muted)] disabled:opacity-60"
    >
      {busy ? "Signing out…" : "Sign out"}
    </button>
  );
}
