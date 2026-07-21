import { beforeAll, describe, expect, it } from "vitest";

import {
  deserializeSession,
  serializeSession,
  type AgentHomeSession,
} from "@/lib/auth/session";
import type { Principal } from "@/types";

const PRINCIPAL: Principal = {
  user_id: "u_alice",
  display: "Alice",
  role: "member",
  channels: [],
  is_owner: false,
};

const SESSION: AgentHomeSession = {
  hermesToken: "upstream-token-xyz",
  principal: PRINCIPAL,
  issuedAt: 1_700_000_000,
};

describe("agent-home session cookie", () => {
  beforeAll(() => {
    process.env.AGENT_HOME_SESSION_SECRET = "test-secret-for-hmac";
  });

  it("round-trips a signed session", () => {
    const cookie = serializeSession(SESSION);
    const parsed = deserializeSession(cookie);
    expect(parsed).toEqual(SESSION);
  });

  it("rejects a tampered payload", () => {
    const cookie = serializeSession(SESSION);
    const [payload, sig] = cookie.split(".");
    // Flip a byte in the payload; the HMAC must no longer verify.
    const tampered = `${payload}x.${sig}`;
    expect(deserializeSession(tampered)).toBeNull();
  });

  it("rejects a forged signature", () => {
    const [payload] = serializeSession(SESSION).split(".");
    expect(deserializeSession(`${payload}.deadbeef`)).toBeNull();
  });

  it("returns null for empty/garbage input", () => {
    expect(deserializeSession(undefined)).toBeNull();
    expect(deserializeSession("")).toBeNull();
    expect(deserializeSession("nodot")).toBeNull();
  });
});
