import type { ReactNode } from "react";

/** Shared badge tones (mirrors the GTS Centre palette). */
export type Tone = "accent" | "success" | "warning" | "danger" | "muted";

const TONE_CLASS: Record<Tone, string> = {
  accent: "bg-[var(--color-accent)] text-[var(--color-accent-fg)]",
  success: "bg-emerald-500/15 text-emerald-300",
  warning: "bg-amber-500/15 text-amber-300",
  danger: "bg-red-500/15 text-red-300",
  muted: "bg-[var(--color-surface-2)] text-[var(--color-fg)]",
};

/** A small rounded status pill used across the mobile feature panels. */
export function Pill({ tone = "muted", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      data-component="Pill"
      className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs ${TONE_CLASS[tone]}`}
    >
      {children}
    </span>
  );
}
