"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Fixed bottom tab navigation — the primary mobile-first nav (FG-20 Wave A1).
 * Large touch targets, clears the phone home indicator via safe-area inset.
 * Tabs: Home, GTS Graph (B1), one-brain Chat (C1), comms Inbox (C3), Activity
 * trace (B2). Secondary surfaces (Core, Onboarding, Tools, Webview) are linked
 * from Home rather than the nav.
 */
const TABS: { href: string; label: string; glyph: string }[] = [
  { href: "/", label: "Home", glyph: "◉" },
  { href: "/graph", label: "Graph", glyph: "◈" },
  { href: "/chat", label: "Chat", glyph: "✦" },
  { href: "/inbox", label: "Inbox", glyph: "✉" },
  { href: "/activity", label: "Activity", glyph: "≋" },
];

export function BottomNav() {
  const pathname = usePathname();
  return (
    <nav
      data-component="BottomNav"
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--color-border)] bg-[var(--color-surface)]/95 backdrop-blur"
      style={{ paddingBottom: "var(--safe-bottom)" }}
    >
      <ul className="mx-auto flex max-w-md items-stretch justify-around">
        {TABS.map((tab) => {
          const active =
            tab.href === "/" ? pathname === "/" : pathname.startsWith(tab.href);
          return (
            <li key={tab.href} className="flex-1">
              <Link
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={`flex h-16 flex-col items-center justify-center gap-1 text-xs ${
                  active ? "text-[var(--color-accent)]" : "text-[var(--color-muted)]"
                }`}
              >
                <span aria-hidden className="text-xl leading-none">
                  {tab.glyph}
                </span>
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
