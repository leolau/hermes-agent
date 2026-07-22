import type { ReactNode } from "react";

import { BottomNav } from "@/components/BottomNav";
import { SideNav } from "@/components/SideNav";

/**
 * The adaptive app shell (FG-20 Wave A1, made responsive).
 *
 * - **Phone (base):** a single phone-width column with a sticky safe-area
 *   header and the fixed `BottomNav` tab bar — unchanged from the original
 *   mobile-first design.
 * - **Tablet (`md`):** the content column widens so it stops looking like a
 *   phone stuck in the middle of the screen.
 * - **Desktop (`lg`+):** the bottom tab bar is replaced by a persistent left
 *   `SideNav`, and the content area fills the remaining width (centred, with a
 *   comfortable max) — a real responsive webapp, not a phone frame.
 *
 * Feature panels render into `children`. `showNav={false}` (e.g. the login
 * page) drops both navs and their reserved space.
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
    <div data-component="MobileShell" className="min-h-dvh bg-[var(--color-bg)] lg:flex">
      {showNav ? <SideNav /> : null}
      <div className="mx-auto flex min-h-dvh w-full max-w-md flex-col md:max-w-2xl lg:mx-0 lg:max-w-none lg:flex-1">
        <header
          className="sticky top-0 z-20 border-b border-[var(--color-border)] bg-[var(--color-bg)]/90 px-4 py-3 backdrop-blur lg:px-8"
          style={{ paddingTop: "calc(var(--safe-top) + 0.75rem)" }}
        >
          <div className="mx-auto w-full max-w-2xl lg:max-w-5xl">
            <h1 className="text-base font-semibold tracking-tight lg:text-lg">
              {title}
            </h1>
          </div>
        </header>
        <main
          className={`flex-1 px-4 py-4 lg:px-8 ${
            showNav
              ? "pb-[calc(var(--bottom-nav-h)+var(--safe-bottom)+1rem)] lg:pb-8"
              : "pb-[calc(var(--safe-bottom)+1rem)]"
          }`}
        >
          <div className="mx-auto w-full max-w-2xl lg:max-w-5xl">{children}</div>
        </main>
        {showNav ? <BottomNav /> : null}
      </div>
    </div>
  );
}
