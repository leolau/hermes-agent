/**
 * POST /api/comms/notifications/{id}/answer — BFF settle a pending FG-10 item
 * (FG-20 Wave C3). Body: `{ answer }` (e.g. "approved" / "denied" /
 * "acknowledged"). Forwards to the Python API under the bridged C1 principal
 * (write path, no `?as=`); settlement is idempotent + de-duplicated across
 * surfaces. This route never decides the answer — it forwards the user's.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

interface AnswerBody {
  answer?: unknown;
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ notificationId: string }> },
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { notificationId } = await params;
  let body: AnswerBody;
  try {
    body = (await request.json()) as AnswerBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const answer = typeof body.answer === "string" ? body.answer.trim() : "";
  if (!answer) {
    return NextResponse.json(
      { error: "invalid_answer", detail: "An 'answer' is required." },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    const resp = await client.answerNotification(notificationId, answer);
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
