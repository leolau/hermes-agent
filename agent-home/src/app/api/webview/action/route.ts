/**
 * POST /api/webview/action — BFF request-an-action (FG-20 Wave C2 / FG-17b).
 *
 * Body: `{ kind, url?, credentialed?, destructive? }`. Forwards to the Python
 * API `POST /api/webview/action` under the bridged C1 principal. The server's
 * Option-B policy decides allow (run via CDP + C8 trace) vs escalate (queue a
 * per-action C6 approval) — this route never decides consent, it forwards.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import type { WebviewActionKind } from "@/types";

interface ActionBody {
  kind?: unknown;
  url?: unknown;
  credentialed?: unknown;
  destructive?: unknown;
}

export async function POST(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  let body: ActionBody;
  try {
    body = (await request.json()) as ActionBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  if (typeof body.kind !== "string" || !body.kind) {
    return NextResponse.json(
      { error: "invalid_action", detail: "An action 'kind' is required." },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    const resp = await client.requestWebviewAction({
      kind: body.kind as WebviewActionKind,
      url: typeof body.url === "string" && body.url.trim() ? body.url.trim() : null,
      credentialed: Boolean(body.credentialed),
      destructive: Boolean(body.destructive),
    });
    return NextResponse.json(resp);
  } catch (err) {
    if (err instanceof HermesApiError) {
      return NextResponse.json(
        { error: "api_error", detail: err.message },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: "api_unreachable", detail: "The AI layer could not be reached." },
      { status: 502 },
    );
  }
}
