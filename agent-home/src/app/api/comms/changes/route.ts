/**
 * GET /api/comms/changes — BFF FG-12 change-log list (FG-20 Wave C3).
 *
 * Forwards to the Python API `GET /api/comms/changes` under the bridged C1
 * principal, returning the C2-scoped reversible/irreversible change events the
 * user may review (and undo/redo). The browser never calls the AI layer.
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
    const resp = await client.changes();
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
