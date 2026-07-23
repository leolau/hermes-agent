/**
 * POST /api/comms/members/{userId}/deactivate — block a member's login without
 * deleting the account (owner/admin). The Python layer bans the GoTrue account;
 * the principal row and any owned data are preserved, so it is reversible via
 * the reactivate route.
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
    return NextResponse.json(await gate.client.deactivateMember(userId));
  } catch (err) {
    return forwardMemberError(err);
  }
}
