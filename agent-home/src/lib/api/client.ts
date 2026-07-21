/**
 * Typed client for the Python AI layer (`/api/*`, `/auth/*`) — FG-20 Wave A2.
 *
 * This is the *only* channel `agent-home` uses for anything agent- or
 * authority-related (one-brain chat, CDP webview, GTS authority writes,
 * onboarding readiness, Core manifest, tool enable/promote, comms). It never
 * re-implements that logic — it forwards to the Python API and replays the
 * bridged Hermes session token so the call is authenticated exactly as the
 * dashboard's own requests are (the gate reads the `hermes_session_at` cookie;
 * see `hermes_cli/dashboard_auth/cookies.py`).
 *
 * Server-only: the browser never calls the Python API directly (BFF pattern),
 * so this module holds the upstream token and runs on the `agent-home` server.
 */
import "server-only";

import { hermesApiBaseUrl } from "@/lib/env";
import type {
  ChangesResponse,
  ChatMessagesResponse,
  ChatSendResponse,
  CoreManifestResponse,
  GtsGraphResponse,
  Notification,
  OnboardingReadinessResponse,
  Principal,
  SessionCreateResponse,
  SessionsResponse,
  StoreMode,
  ToolsResponse,
  TraceDetailResponse,
  TracesResponse,
} from "@/types";

/** Raised when the Python API returns a non-2xx status. */
export class HermesApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "HermesApiError";
  }
}

export interface HermesApiClientOptions {
  /** The bridged upstream Hermes access token to replay (from the session). */
  hermesToken?: string;
  /** Override the base URL (tests / non-default topology). */
  baseUrl?: string;
}

/**
 * A thin, typed `fetch` wrapper around the Python API. Construct one per
 * request from the bridged session token; methods return parsed JSON typed to
 * the shared entity shapes.
 */
export class HermesApiClient {
  private readonly baseUrl: string;
  private readonly hermesToken?: string;

  constructor(opts: HermesApiClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? hermesApiBaseUrl()).replace(/\/+$/, "");
    this.hermesToken = opts.hermesToken;
  }

  /** Low-level request. Prefer the typed methods below where they exist. */
  async request<T>(
    path: string,
    init: RequestInit & { json?: unknown } = {},
  ): Promise<T> {
    const { json, headers, ...rest } = init;
    const finalHeaders = new Headers(headers);
    if (this.hermesToken) {
      // Replay the bridged session both as the dashboard cookie the gate reads
      // and as a bearer header, so either verification path accepts it.
      finalHeaders.set("cookie", `hermes_session_at=${this.hermesToken}`);
      finalHeaders.set("authorization", `Bearer ${this.hermesToken}`);
    }
    if (json !== undefined) {
      finalHeaders.set("content-type", "application/json");
    }
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...rest,
      headers: finalHeaders,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
      // Server-to-server on the same box: never cache authority responses.
      cache: "no-store",
    });
    const text = await res.text();
    const parsed = text ? safeJson(text) : undefined;
    if (!res.ok) {
      throw new HermesApiError(
        res.status,
        `Hermes API ${path} → ${res.status}`,
        parsed ?? text,
      );
    }
    return parsed as T;
  }

  /** Resolve the C1 principal + role for the current bridged session. */
  async whoami(): Promise<{ configured: boolean; principal: Principal | null }> {
    return this.request("/api/comms/whoami");
  }

  /** List the interactive auth providers (login-page bootstrap). Unauthed. */
  async authProviders(): Promise<{
    providers: { name: string; display_name: string; supports_password: boolean }[];
  }> {
    return this.request("/api/auth/providers");
  }

  /**
   * The FG-18 GTS Centre graph (C9) scoped to the principal (C2 + item_grants
   * RLS, enforced server-side in the Python layer). Read-only: creation and
   * scoring stay on the CLI/agent authority paths, so there is no write here.
   */
  async gtsGraph(): Promise<GtsGraphResponse> {
    return this.request("/api/gts/graph");
  }

  /**
   * The FG-14 C7 Core-boundary projection (read-only): active manifest globs,
   * boundary health, and the tail of the Core-denial audit log. Core is
   * immutable to the runtime agent, so this only reflects the boundary.
   */
  async coreManifest(limit = 50): Promise<CoreManifestResponse> {
    return this.request(`/api/core/manifest?limit=${encodeURIComponent(limit)}`);
  }

  /**
   * The C2-scoped list of C8 interaction traces (read-only). Scoping is
   * enforced upstream by the Python ledger; the browser never sees traces the
   * principal may not.
   */
  async traces(limit = 50): Promise<TracesResponse> {
    return this.request(`/api/comms/traces?limit=${encodeURIComponent(limit)}`);
  }

  /** One trace's C2-scoped interaction timeline + rollup (read-only). */
  async trace(traceId: string): Promise<TraceDetailResponse> {
    return this.request(`/api/comms/traces/${encodeURIComponent(traceId)}`);
  }

  /** The C2-scoped FG-12 change log (read-only in this surface). */
  async changes(): Promise<ChangesResponse> {
    return this.request("/api/comms/changes");
  }

  /**
   * The FG-15 onboarding readiness (read-only): the CLI's setup schema +
   * `ready_for_prod` gate. Reports secret *presence* only, never values.
   */
  async onboardingReadiness(): Promise<OnboardingReadinessResponse> {
    return this.request("/api/onboarding/readiness");
  }

  /**
   * The FG-07 tool registry for a datastore mode (read-only in this surface).
   * Enable/config/promote stay on the operator authority paths.
   */
  async tools(mode?: StoreMode): Promise<ToolsResponse> {
    const qs = mode ? `?mode=${encodeURIComponent(mode)}` : "";
    return this.request(`/api/tools${qs}`);
  }

  /**
   * List the principal's conversations (read path). Defaults to the
   * `agent_home` source ordered by most-recent activity so the mobile chat
   * list surfaces the conversations started from this app first.
   */
  async sessions(
    opts: { source?: string; limit?: number; order?: "created" | "recent" } = {},
  ): Promise<SessionsResponse> {
    const params = new URLSearchParams();
    if (opts.source) params.set("source", opts.source);
    params.set("limit", String(opts.limit ?? 30));
    params.set("order", opts.order ?? "recent");
    return this.request(`/api/sessions?${params.toString()}`);
  }

  /** Load one conversation's persisted transcript (read path). */
  async sessionMessages(sessionId: string): Promise<ChatMessagesResponse> {
    return this.request(
      `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
    );
  }

  /**
   * Create a new conversation (owner-attributed) via `POST /api/sessions`.
   * Idempotent server-side: a supplied id that already exists is a 409.
   */
  async createSession(sessionId?: string): Promise<SessionCreateResponse> {
    return this.request("/api/sessions", {
      method: "POST",
      json: sessionId ? { session_id: sessionId } : {},
    });
  }

  /**
   * Send one one-brain turn to a conversation via
   * `POST /api/sessions/{id}/chat` and return the assistant reply. The turn is
   * driven by the shared `AIAgent` + `SessionDB` under the C1 principal — this
   * client never re-implements the conversation loop, it forwards the message.
   */
  async sendChat(
    sessionId: string,
    message: string,
  ): Promise<ChatSendResponse> {
    return this.request(
      `/api/sessions/${encodeURIComponent(sessionId)}/chat`,
      { method: "POST", json: { message } },
    );
  }

  /** List pending comms/notifications visible to the principal (C2-scoped). */
  async notifications(): Promise<{
    configured: boolean;
    notifications: Notification[];
  }> {
    return this.request("/api/comms/notifications");
  }
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
