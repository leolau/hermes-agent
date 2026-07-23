/**
 * POST /api/comms/members/{userId}/activate — restore a deactivated member's
 * login (owner/admin). The Python layer unbans the GoTrue account.
 */
import { NextResponse } from "next/server";

import { forwardMemberError, requireMemberAdmin } from "@/lib/api/member-bff";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ userId: string }> },
): Promise<NextResponse> {
  const gate = await requireMemberAdmin();
  if ("response" in gate) return gate.response;
  const { userId } = await params;
  try {
    return NextResponse.json(await gate.client.activateMember(userId));
  } catch (err) {
    return forwardMemberError(err);
  }
}
