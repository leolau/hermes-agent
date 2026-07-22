"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { isActive, PRIMARY_NAV } from "@/components/nav-items";

/**
 * Fixed bottom tab navigation — the primary mobile-first nav (FG-20 Wave A1).
 * Large touch targets, clears the phone home indicator via safe-area inset.
 * Hidden at `lg`+, where the persistent `SideNav` takes over. Tabs come from
 * the shared `nav-items` model; secondary surfaces (Core, Onboarding, Tools,
 * Webview) are linked from Home on mobile and from the sidebar on desktop.
 */
export function BottomNav() {
  const pathname = usePathname();
  return (
    <nav
      data-component="BottomNav"
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--color-border)] bg-[var(--color-surface)]/95 backdrop-blur lg:hidden"
      style={{ paddingBottom: "var(--safe-bottom)" }}
    >
      <ul className="mx-auto flex max-w-md items-stretch justify-around md:max-w-2xl">
        {PRIMARY_NAV.map((tab) => {
          const active = isActive(pathname, tab.href);
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
