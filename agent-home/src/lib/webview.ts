/**
 * Pure helpers for the FG-20 Wave C2 agent-webview panel. Kept free of
 * React/DOM so the node-environment vitest suite can exercise them directly;
 * the consent policy itself is enforced server-side in `hermes_cli/webview.py`
 * (this surface never re-implements it).
 */
import type { WebviewDecision } from "@/types";

/**
 * Parse the comma-separated "allowed domains" consent input into a clean list
 * (trimmed, non-empty, de-duplicated, order-preserving).
 */
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

/** A decision's mobile-pill tone class (green allow / amber escalate / red deny). */
export type DecisionTone = "success" | "warning" | "danger";

/** Map a policy decision to the tone the mobile console renders. */
export function decisionTone(decision: WebviewDecision): DecisionTone {
  switch (decision) {
    case "allow":
      return "success";
    case "deny":
      return "danger";
    default:
      return "warning";
  }
}
