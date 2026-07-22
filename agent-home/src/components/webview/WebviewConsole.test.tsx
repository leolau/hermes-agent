import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WebviewConsole } from "@/components/webview/WebviewConsole";
import type { WebviewSession } from "@/types";

const SESSION: WebviewSession = {
  id: "wv_1",
  owner_user_id: "leo_owner",
  scope: { allowed_domains: ["example.com"], mode: "read_only" },
  profile_dir: "/profiles/abc",
  created_at: 1,
  trace_id: "trace_0123456789abcdef0000",
  pending: [
    {
      id: "wva_1",
      kind: "submit",
      url: "https://example.com/buy",
      credentialed: false,
      destructive: true,
      reason: "destructive action requires approval",
      created_at: 2,
      resolved: null,
    },
  ],
};

describe("WebviewConsole", () => {
  it("renders the default-deny opt-in consent form when no session is open", () => {
    const html = renderToStaticMarkup(
      <WebviewConsole initialConfigured initialSession={null} />,
    );
    expect(html).toContain('data-component="WebviewConsole"');
    expect(html).toContain('data-component="ConsentForm"');
    expect(html).toContain("default-deny");
    expect(html).toContain("Allowed domains");
    expect(html).toContain("consent-gated (C6)");
    expect(html).toContain("traced (C8)");
  });

  it("renders the open session, action console, and a pending C6 approval", () => {
    const html = renderToStaticMarkup(
      <WebviewConsole initialConfigured initialSession={SESSION} />,
    );
    expect(html).toContain('data-component="WebviewSessionCard"');
    expect(html).toContain('data-component="ActionConsole"');
    expect(html).toContain('data-component="PendingApprovals"');
    expect(html).toContain("Session open");
    expect(html).toContain("read_only");
    expect(html).toContain("example.com");
    expect(html).toContain("destructive action requires approval");
    expect(html).toContain("Approve");
    expect(html).toContain("Deny");
  });

  it("shows the unconfigured datastore state", () => {
    const html = renderToStaticMarkup(
      <WebviewConsole initialConfigured={false} initialSession={null} />,
    );
    expect(html).toContain("multi-user datastore configured");
  });
});
