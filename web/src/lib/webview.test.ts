import { describe, expect, it } from "vitest";

import { decisionTone, parseAllowedDomains } from "@/lib/webview";

describe("parseAllowedDomains", () => {
  it("trims, drops empties, and de-duplicates while preserving order", () => {
    expect(parseAllowedDomains(" example.com , docs.internal ,, example.com ")).toEqual([
      "example.com",
      "docs.internal",
    ]);
  });

  it("returns an empty list for blank input", () => {
    expect(parseAllowedDomains("")).toEqual([]);
    expect(parseAllowedDomains("  ,  , ")).toEqual([]);
  });
});

describe("decisionTone", () => {
  it("maps each policy decision to its badge tone", () => {
    expect(decisionTone("allow")).toBe("success");
    expect(decisionTone("deny")).toBe("destructive");
    expect(decisionTone("escalate")).toBe("warning");
  });
});
