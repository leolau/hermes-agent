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

describe("HermesApiClient Wave B2 reads (BFF forwarding)", () => {
  it("coreManifest GETs /api/core/manifest with the limit + replays the token", async () => {
    const payload = {
      core_root: "hermes-agent",
      manifest_path: "core_manifest.yaml",
      manifest_present: true,
      manifest_parseable: true,
      fallback_active: false,
      self_protected: true,
      globs: ["agent/**"],
      glob_count: 1,
      audit_log_path: "/tmp/core_audit.log",
      denials: [],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({
      hermesToken: "tok-123",
      baseUrl: "http://api.test",
    });
    const manifest = await client.coreManifest(50);

    expect(manifest).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/core/manifest?limit=50");
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBe("hermes_session_at=tok-123");
    expect(headers.get("authorization")).toBe("Bearer tok-123");
    expect(init?.cache).toBe("no-store");
  });

  it("traces GETs /api/comms/traces with the limit query", async () => {
    const payload = { configured: true, principal: "owner", traces: [] };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.traces(50);

    expect(res).toEqual(payload);
    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/api/comms/traces?limit=50");
  });

  it("trace GETs /api/comms/traces/{id} (id encoded)", async () => {
    const payload = {
      configured: true,
      trace_id: "trc_a b",
      interactions: [],
      rollup: null,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.trace("trc_a b");

    expect(res).toEqual(payload);
    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/api/comms/traces/trc_a%20b");
  });

  it("changes GETs /api/comms/changes", async () => {
    const payload = { configured: true, principal: "owner", changes: [] };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.changes();

    expect(res).toEqual(payload);
    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/api/comms/changes");
  });

  it("throws HermesApiError on a non-2xx upstream", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("boom", { status: 500 }),
    );
    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await expect(client.coreManifest()).rejects.toBeInstanceOf(HermesApiError);
  });
});
