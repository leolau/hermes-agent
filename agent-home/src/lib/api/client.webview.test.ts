import { afterEach, describe, expect, it, vi } from "vitest";

import { HermesApiClient, HermesApiError } from "@/lib/api/client";

afterEach(() => {
  vi.restoreAllMocks();
});

function ok(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("HermesApiClient Wave C2 webview (BFF forwarding)", () => {
  it("getWebviewSession GETs /api/webview/session and replays the token", async () => {
    const payload = { configured: true, principal: "leo_owner", session: null };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({
      hermesToken: "tok-abc",
      baseUrl: "http://api.test",
    });
    const res = await client.getWebviewSession();

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/webview/session");
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBe("hermes_session_at=tok-abc");
    expect(init?.cache).toBe("no-store");
  });

  it("openWebviewSession POSTs the consent scope", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ configured: true, session: null }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.openWebviewSession({
      allowed_domains: ["example.com"],
      mode: "interactive",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/webview/session");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      allowed_domains: ["example.com"],
      mode: "interactive",
    });
  });

  it("closeWebviewSession DELETEs the session", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, closed: true }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.closeWebviewSession();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/webview/session");
    expect(init?.method).toBe("DELETE");
  });

  it("requestWebviewAction POSTs the action", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ decision: "allow", reason: "in-scope" }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.requestWebviewAction({
      kind: "navigate",
      url: "https://example.com",
      credentialed: false,
      destructive: false,
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/webview/action");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      kind: "navigate",
      url: "https://example.com",
      credentialed: false,
      destructive: false,
    });
  });

  it("resolveWebviewApproval POSTs the grant to the encoded approval path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ decision: "allow", granted: true }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.resolveWebviewApproval("wva id/1", true);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/webview/approval/wva%20id%2F1");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ grant: true });
  });

  it("throws HermesApiError on a non-2xx upstream", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "default-deny" }), { status: 403 }),
    );
    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await expect(
      client.requestWebviewAction({ kind: "navigate" }),
    ).rejects.toBeInstanceOf(HermesApiError);
  });
});
