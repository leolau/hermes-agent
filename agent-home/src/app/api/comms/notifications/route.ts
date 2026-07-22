/**
 * GET /api/comms/notifications — BFF inbox list (FG-20 Wave C3 / FG-10).
 *
 * Forwards to the Python API `GET /api/comms/notifications` under the bridged
 * C1 principal, returning the C2-scoped pending approvals + proactive asks.
 * The browser never calls the AI layer; scoping stays in Python.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function GET(): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    const client = await apiClientForRequest();
    const resp = await client.notifications();
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
