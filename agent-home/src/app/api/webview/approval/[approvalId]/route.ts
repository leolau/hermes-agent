/**
 * POST /api/webview/approval/{approvalId} — BFF resolve a C6 approval (FG-17b).
 *
 * Body: `{ grant: boolean }`. Forwards to the Python API
 * `POST /api/webview/approval/{id}` under the bridged C1 principal. On grant the
 * escalated action runs through the same CDP handoff and is traced (C8); on deny
 * it is discarded. This route never decides consent — the user's grant does.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

interface ResolveBody {
  grant?: unknown;
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ approvalId: string }> },
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { approvalId } = await params;
  let body: ResolveBody;
  try {
    body = (await request.json()) as ResolveBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  try {
    const client = await apiClientForRequest();
    const resp = await client.resolveWebviewApproval(
      approvalId,
      Boolean(body.grant),
    );
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
