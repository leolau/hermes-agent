// Pure helpers for the FG-17b agent-webview panel. Kept free of React/DOM so
// the node-environment vitest suite can exercise them directly; the policy
// itself is enforced server-side in ``hermes_cli/webview.py``.

import type { WebviewActionResponse } from "@/lib/api";

/** Parse the comma-separated "allowed domains" consent input into a clean list
 * (trimmed, non-empty, de-duplicated, order-preserving). */
export function parseAllowedDomains(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const d = part.trim();
    if (d && !seen.has(d)) {
      seen.add(d);
      out.push(d);
    }
  }
  return out;
}

export type BadgeTone = "success" | "destructive" | "warning";

/** Map a policy decision to the badge tone the panel renders. */
export function decisionTone(decision: WebviewActionResponse["decision"]): BadgeTone {
  switch (decision) {
    case "allow":
      return "success";
    case "deny":
      return "destructive";
    default:
      return "warning";
  }
}
