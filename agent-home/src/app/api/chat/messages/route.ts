/**
 * GET /api/chat/messages?sessionId=… — BFF read of one conversation's
 * transcript (FG-20 Wave C1). Forwards to the Python API under the bridged C1
 * principal; the browser never calls the AI layer directly.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function GET(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const sessionId = new URL(request.url).searchParams.get("sessionId");
  if (!sessionId) {
    return NextResponse.json(
      { error: "missing_session", detail: "sessionId is required" },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    const data = await client.sessionMessages(sessionId);
    return NextResponse.json(data);
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
