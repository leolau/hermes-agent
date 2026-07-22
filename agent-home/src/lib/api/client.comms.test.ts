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

describe("HermesApiClient Wave C3 comms (BFF forwarding)", () => {
  it("notifications GETs /api/comms/notifications and replays the token", async () => {
    const payload = {
      configured: true,
      principal: "leo_owner",
      notifications: [],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({
      hermesToken: "tok-abc",
      baseUrl: "http://api.test",
    });
    const res = await client.notifications();

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/notifications");
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBe("hermes_session_at=tok-abc");
    expect(init?.cache).toBe("no-store");
  });

  it("answerNotification POSTs the answer to the encoded item path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, newly_answered: true, notification: {} }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.answerNotification("ntf id/1", "approved");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/notifications/ntf%20id%2F1/answer");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ answer: "approved" });
  });

  it("undoChange POSTs to the encoded undo path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, change_ref: "c1", target_kind: "memory" }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.undoChange("chg 1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/changes/chg%201/undo");
    expect(init?.method).toBe("POST");
  });

  it("redoChange POSTs to the encoded redo path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, change_ref: "c1", target_kind: "memory" }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.redoChange("chg 1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/changes/chg%201/redo");
    expect(init?.method).toBe("POST");
  });

  it("throws HermesApiError on a non-2xx upstream", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "not visible" }), { status: 403 }),
    );
    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await expect(client.undoChange("c1")).rejects.toBeInstanceOf(HermesApiError);
  });
});
