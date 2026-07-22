"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { isActive, PRIMARY_NAV, SECONDARY_NAV, type NavItem } from "@/components/nav-items";

/**
 * Desktop/tablet-wide left sidebar (FG-20 adaptive shell). Hidden below `lg`,
 * where the fixed `BottomNav` is the primary navigation instead. Shares its
 * destinations with `BottomNav` via `nav-items` so the two never drift, and
 * adds a "More" section for the secondary surfaces that only fit here.
 */
function SideLink({ item, active }: { item: NavItem; active: boolean }) {
  return (
    <li>
      <Link
        href={item.href}
        aria-current={active ? "page" : undefined}
        className={`flex items-center gap-3 rounded-xl px-3 py-2 text-sm ${
          active
            ? "bg-[var(--color-surface-2)] text-[var(--color-accent)]"
            : "text-[var(--color-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
        }`}
      >
        <span aria-hidden className="text-lg leading-none">
          {item.glyph}
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="truncate">{item.label}</span>
          {item.hint ? (
            <span className="truncate text-xs text-[var(--color-muted)]">
              {item.hint}
            </span>
          ) : null}
        </span>
      </Link>
    </li>
  );
}

export function SideNav() {
  const pathname = usePathname();
  return (
    <aside
      data-component="SideNav"
      aria-label="Primary"
      className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] lg:flex"
      style={{ paddingTop: "var(--safe-top)" }}
    >
      <div className="px-5 py-4">
        <p className="text-base font-semibold tracking-tight">Agent Home</p>
        <p className="text-xs text-[var(--color-muted)]">Hermes · mobile-first</p>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        <ul className="flex flex-col gap-1">
          {PRIMARY_NAV.map((item) => (
            <SideLink key={item.href} item={item} active={isActive(pathname, item.href)} />
          ))}
        </ul>
        <p className="px-3 pb-1 pt-4 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
          More
        </p>
        <ul className="flex flex-col gap-1">
          {SECONDARY_NAV.map((item) => (
            <SideLink key={item.href} item={item} active={isActive(pathname, item.href)} />
          ))}
        </ul>
      </nav>
    </aside>
  );
}
