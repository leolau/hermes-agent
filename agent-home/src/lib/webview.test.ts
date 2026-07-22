import { describe, expect, it } from "vitest";

import { decisionTone, parseAllowedDomains } from "@/lib/webview";

describe("parseAllowedDomains", () => {
  it("trims, drops empties, and de-duplicates while preserving order", () => {
    expect(parseAllowedDomains(" example.com , , docs.internal, example.com ")).toEqual([
      "example.com",
      "docs.internal",
    ]);
  });

  it("returns an empty list for blank input", () => {
    expect(parseAllowedDomains("   ")).toEqual([]);
  });
});

describe("decisionTone", () => {
  it("maps allow → success, escalate → warning, deny → danger", () => {
    expect(decisionTone("allow")).toBe("success");
    expect(decisionTone("escalate")).toBe("warning");
    expect(decisionTone("deny")).toBe("danger");
  });
});
