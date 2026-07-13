// Pure helpers for the FG-17b embedded-Telegram pane. The pane surfaces the
// real Telegram channel status (from the messaging-platforms API) and routes
// the in-dashboard conversation to the same FG-03 one-brain backend as the
// Telegram app (D13). Kept DOM-free for the node-environment vitest suite.

import type { MessagingPlatform } from "@/lib/api";

export interface TelegramChannelView {
  platform: MessagingPlatform | null;
  /** True when the Telegram channel is configured AND the gateway reports it
   * connected — i.e. the Telegram app surface of the one-brain backend is live. */
  connected: boolean;
  /** A short, human-readable status for the pane header. */
  status: string;
}

export function resolveTelegramChannel(
  platforms: MessagingPlatform[],
): TelegramChannelView {
  const platform =
    platforms.find((p) => p.id === "telegram") ??
    platforms.find((p) => p.id.startsWith("telegram")) ??
    null;
  if (platform === null) {
    return { platform: null, connected: false, status: "unavailable" };
  }
  const connected = platform.state === "connected";
  let status: string;
  if (connected) status = "connected";
  else if (!platform.configured) status = "not configured";
  else if (!platform.enabled) status = "disabled";
  else status = platform.state || "disconnected";
  return { platform, connected, status };
}
