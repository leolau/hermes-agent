import { afterEach, describe, expect, it, vi } from "vitest";

import { HermesApiClient } from "@/lib/api/client";

afterEach(() => {
  vi.restoreAllMocks();
});

function ok(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("HermesApiClient member management (PR-4 BFF forwarding)", () => {
  it("members() GETs /api/comms/members and replays the token", async () => {
    const payload = { configured: true, members: [] };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({
      hermesToken: "tok-abc",
      baseUrl: "http://api.test",
    });
    const res = await client.members();

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/members");
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBe("hermes_session_at=tok-abc");
    expect(init?.cache).toBe("no-store");
  });

  it("createMember() POSTs the account fields as JSON", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, member: { user_id: "u", display: "M", role: "member" } }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.createMember({
      email: "m@x.io",
      password: "temp-123",
      display: "Mia",
      role: "member",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/members");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      email: "m@x.io",
      password: "temp-123",
      display: "Mia",
      role: "member",
    });
  });

  it("setMemberRole() PUTs the role to the encoded member path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, member: { user_id: "u/1", role: "admin" } }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.setMemberRole("u/1", "admin");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/members/u%2F1/role");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ role: "admin" });
  });

  it("setMemberPassword() POSTs the password to the encoded path", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok({ ok: true }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.setMemberPassword("u1", "new-temp");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/members/u1/password");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ password: "new-temp" });
  });

  it("deactivateMember()/activateMember() POST to the right paths", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => ok({ ok: true, active: false }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.deactivateMember("u1");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/api/comms/members/u1/deactivate",
    );
    await client.activateMember("u1");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "http://api.test/api/comms/members/u1/activate",
    );
  });
});
