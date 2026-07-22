/**
 * /api/webview/session — BFF for the FG-20 Wave C2 agent webview (FG-17b).
 *
 * GET returns the caller's open session (or the default-deny empty state);
 * POST opts in with a consent scope; DELETE opts out. All three forward to the
 * Python API `/api/webview/session` under the bridged C1 principal, which owns
 * the consent policy (C6), per-user CDP-profile isolation (C2), and tracing
 * (C8). The browser never calls the AI layer directly and never decides consent.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import type { WebviewMode } from "@/types";

interface OpenBody {
  allowed_domains?: unknown;
  mode?: unknown;
}

function apiError(err: unknown): NextResponse {
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

export async function GET(): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.getWebviewSession());
  } catch (err) {
    return apiError(err);
  }
}

export async function POST(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  let body: OpenBody;
  try {
    body = (await request.json()) as OpenBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const allowed_domains = Array.isArray(body.allowed_domains)
    ? body.allowed_domains.filter((d): d is string => typeof d === "string")
    : [];
  const mode: WebviewMode = body.mode === "interactive" ? "interactive" : "read_only";
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(
      await client.openWebviewSession({ allowed_domains, mode }),
    );
  } catch (err) {
    return apiError(err);
  }
}

export async function DELETE(): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.closeWebviewSession());
  } catch (err) {
    return apiError(err);
  }
}
