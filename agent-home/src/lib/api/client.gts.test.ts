import { afterEach, describe, expect, it, vi } from "vitest";

import { HermesApiClient, HermesApiError } from "@/lib/api/client";
import type { GtsGraphResponse } from "@/types";

const SAMPLE: GtsGraphResponse = {
  configured: true,
  principal: "owner",
  mode: "prod",
  goals: [],
  tasks: [],
  skills: [],
  task_goals: [],
  task_skills: [],
  assignment: { enabled: true, scheme: "per-user" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("HermesApiClient.gtsGraph (BFF forwarding)", () => {
  it("GETs /api/gts/graph and replays the bridged token (cookie + bearer)", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify(SAMPLE), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );

    const client = new HermesApiClient({
      hermesToken: "tok-123",
      baseUrl: "http://api.test",
    });
    const graph = await client.gtsGraph();

    expect(graph).toEqual(SAMPLE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/gts/graph");
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBe("hermes_session_at=tok-123");
    expect(headers.get("authorization")).toBe("Bearer tok-123");
    // Authority reads are never cached.
    expect(init?.cache).toBe("no-store");
  });

  it("throws HermesApiError on a non-2xx upstream (e.g. whoami 500 → login)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("boom", { status: 500 }),
    );
    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await expect(client.gtsGraph()).rejects.toBeInstanceOf(HermesApiError);
  });
});
