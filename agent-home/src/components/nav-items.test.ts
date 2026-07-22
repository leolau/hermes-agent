import { describe, expect, it } from "vitest";

import { isActive, PRIMARY_NAV, SECONDARY_NAV } from "@/components/nav-items";

// The mobile BottomNav and desktop SideNav share this model, so the active-tab
// rule must match exactly and the two surfaces must cover the same routes.
describe("nav-items", () => {
  it("matches the root tab only on an exact path", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/graph", "/")).toBe(false);
  });

  it("matches non-root tabs on a path prefix (nested routes stay active)", () => {
    expect(isActive("/activity", "/activity")).toBe(true);
    expect(isActive("/activity/abc123", "/activity")).toBe(true);
    expect(isActive("/inbox", "/activity")).toBe(false);
  });

  it("keeps primary and secondary destinations distinct", () => {
    const primary = new Set(PRIMARY_NAV.map((i) => i.href));
    for (const item of SECONDARY_NAV) {
      expect(primary.has(item.href)).toBe(false);
    }
    expect(PRIMARY_NAV.map((i) => i.href)).toContain("/");
  });
});
