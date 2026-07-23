/**
 * POST /api/comms/members/{userId}/password — reset a member's temporary
 * password (owner/admin). The password is forwarded to the Python layer (which
 * calls GoTrue's admin API) and never stored or logged. Body: `{ password }`.
 */
import { NextResponse } from "next/server";

import { forwardMemberError, requireMemberAdmin } from "@/lib/api/member-bff";

interface PasswordBody {
  password?: unknown;
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ userId: string }> },
): Promise<NextResponse> {
  const gate = await requireMemberAdmin();
  if ("response" in gate) return gate.response;
  const { userId } = await params;
  let body: PasswordBody;
  try {
    body = (await request.json()) as PasswordBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const password = typeof body.password === "string" ? body.password : "";
  if (!password) {
    return NextResponse.json(
      { error: "invalid_input", detail: "A password is required." },
      { status: 400 },
    );
  }
  try {
    return NextResponse.json(await gate.client.setMemberPassword(userId, password));
  } catch (err) {
    return forwardMemberError(err);
  }
}
