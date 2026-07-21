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

describe("HermesApiClient Wave B3 reads (BFF forwarding)", () => {
  it("onboardingReadiness GETs /api/onboarding/readiness and replays the token", async () => {
    const payload = {
      score: 0.8,
      score_pct: 80,
      ready_for_prod: false,
      required_total: 3,
      required_met: 2,
      optional_total: 4,
      optional_met: 1,
      optional_coverage: 0.25,
      missing_required: ["supabase_dsn"],
      items: [],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({
      hermesToken: "tok-123",
      baseUrl: "http://api.test",
    });
    const res = await client.onboardingReadiness();

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/onboarding/readiness");
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBe("hermes_session_at=tok-123");
    expect(headers.get("authorization")).toBe("Bearer tok-123");
    expect(init?.cache).toBe("no-store");
  });

  it("tools GETs /api/tools with no mode by default", async () => {
    const payload = { configured: true, mode: "prod", tools: [] };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.tools();

    expect(res).toEqual(payload);
    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/api/tools");
  });

  it("tools GETs /api/tools?mode=dev when a mode is passed", async () => {
    const payload = { configured: true, mode: "dev", tools: [] };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.tools("dev");

    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/api/tools?mode=dev");
  });

  it("throws HermesApiError on a non-2xx upstream", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("boom", { status: 500 }),
    );
    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await expect(client.onboardingReadiness()).rejects.toBeInstanceOf(HermesApiError);
  });
});
