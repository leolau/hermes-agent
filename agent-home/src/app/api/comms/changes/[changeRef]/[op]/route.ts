/**
 * POST /api/comms/changes/{ref}/{undo|redo} — BFF reverse/reapply an FG-12
 * change (FG-20 Wave C3). Forwards to the Python API under the bridged C1
 * principal (write path, no `?as=`); C2 visibility + D6 reversibility are
 * enforced upstream — this route only validates the op and forwards.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

const OPS = new Set(["undo", "redo"]);

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ changeRef: string; op: string }> },
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { changeRef, op } = await params;
  if (!OPS.has(op)) {
    return NextResponse.json(
      { error: "invalid_op", detail: "op must be 'undo' or 'redo'." },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    const resp =
      op === "undo"
        ? await client.undoChange(changeRef)
        : await client.redoChange(changeRef);
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
