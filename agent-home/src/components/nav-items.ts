/**
 * Shared navigation model for the adaptive shell (FG-20). The same items drive
 * the mobile `BottomNav` (primary tabs) and the desktop `SideNav` (primary +
 * secondary sections), so the two surfaces never drift apart.
 */
export interface NavItem {
  href: string;
  label: string;
  glyph: string;
  hint?: string;
}

/** Primary destinations — the phone bottom-tab bar and the top of the sidebar. */
export const PRIMARY_NAV: NavItem[] = [
  { href: "/", label: "Home", glyph: "◉" },
  { href: "/graph", label: "Graph", glyph: "◈", hint: "GTS Centre" },
  { href: "/chat", label: "Chat", glyph: "✦", hint: "One-brain chat" },
  { href: "/inbox", label: "Inbox", glyph: "✉", hint: "Approvals + changes" },
  { href: "/activity", label: "Activity", glyph: "≋", hint: "Interaction traces" },
];

/**
 * Secondary destinations — linked from Home on mobile, and given a dedicated
 * "More" section in the desktop sidebar where there is room for them.
 */
export const SECONDARY_NAV: NavItem[] = [
  { href: "/onboarding", label: "Getting started", glyph: "◐", hint: "FG-15 readiness" },
  { href: "/tools", label: "Tools", glyph: "⚙", hint: "FG-07 registry" },
  { href: "/core", label: "Core area", glyph: "▣", hint: "C7 boundary" },
  { href: "/webview", label: "Agent webview", glyph: "◔", hint: "FG-17b CDP" },
];

/** Whether `pathname` should mark `href` active (root only matches exactly). */
export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}
