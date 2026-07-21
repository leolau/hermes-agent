import type { ReactNode } from "react";

import { BottomNav } from "@/components/BottomNav";

/**
 * The mobile-first app shell (FG-20 Wave A1): a phone-width column with a
 * sticky top header (safe-area aware), a scrollable content region padded to
 * clear the fixed bottom-nav, and the bottom tab navigation. Feature panels
 * render into `children`.
 */
export function MobileShell({
  title,
  children,
  showNav = true,
}: {
  title: string;
  children: ReactNode;
  showNav?: boolean;
}) {
  return (
    <div data-component="MobileShell" className="min-h-dvh bg-[var(--color-bg)]">
      <div className="mx-auto flex min-h-dvh max-w-md flex-col">
        <header
          className="sticky top-0 z-20 border-b border-[var(--color-border)] bg-[var(--color-bg)]/90 px-4 py-3 backdrop-blur"
          style={{ paddingTop: "calc(var(--safe-top) + 0.75rem)" }}
        >
          <h1 className="text-base font-semibold tracking-tight">{title}</h1>
        </header>
        <main
          className="flex-1 px-4 py-4"
          style={{
            paddingBottom: showNav
              ? "calc(var(--bottom-nav-h) + var(--safe-bottom) + 1rem)"
              : "calc(var(--safe-bottom) + 1rem)",
          }}
        >
          {children}
        </main>
        {showNav ? <BottomNav /> : null}
      </div>
    </div>
  );
}
