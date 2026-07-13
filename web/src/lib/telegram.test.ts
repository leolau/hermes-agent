import { describe, expect, it } from "vitest";

import { resolveTelegramChannel } from "@/lib/telegram";
import type { MessagingPlatform } from "@/lib/api";

function platform(over: Partial<MessagingPlatform>): MessagingPlatform {
  return {
    id: "telegram",
    name: "Telegram",
    description: "",
    docs_url: "",
    enabled: true,
    configured: true,
    gateway_running: true,
    state: "connected",
    error_code: null,
    error_message: null,
    updated_at: null,
    home_channel: null,
    env_vars: [],
    ...over,
  };
}

describe("resolveTelegramChannel", () => {
  it("reports connected when the telegram channel state is connected", () => {
    const view = resolveTelegramChannel([platform({})]);
    expect(view.connected).toBe(true);
    expect(view.status).toBe("connected");
    expect(view.platform?.id).toBe("telegram");
  });

  it("is unavailable when there is no telegram platform", () => {
    const view = resolveTelegramChannel([
      platform({ id: "discord", name: "Discord" }),
    ]);
    expect(view.platform).toBeNull();
    expect(view.connected).toBe(false);
    expect(view.status).toBe("unavailable");
  });

  it("distinguishes not-configured / disabled / disconnected", () => {
    expect(
      resolveTelegramChannel([platform({ configured: false, state: "not_configured" })])
        .status,
    ).toBe("not configured");
    expect(
      resolveTelegramChannel([platform({ enabled: false, state: "disabled" })]).status,
    ).toBe("disabled");
    expect(
      resolveTelegramChannel([platform({ state: "disconnected" })]).status,
    ).toBe("disconnected");
  });
});
