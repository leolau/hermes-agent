/**
 * PUT /api/comms/members/{userId}/role — change a member's role (owner/admin).
 *
 * Forwards to the Python API, which guards the single-owner invariant (never
 * assigns `owner`, never re-roles the current owner — that goes through
 * `hermes owner transfer`). Body: `{ role: "admin" | "member" | "viewer" }`.
 */
import { NextResponse } from "next/server";

import { forwardMemberError, requireMemberAdmin } from "@/lib/api/member-bff";

interface RoleBody {
  role?: unknown;
}

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ userId: string }> },
): Promise<NextResponse> {
  const gate = await requireMemberAdmin();
  if ("response" in gate) return gate.response;
  const { userId } = await params;
  let body: RoleBody;
  try {
    body = (await request.json()) as RoleBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const role = typeof body.role === "string" ? body.role.trim() : "";
  if (role !== "admin" && role !== "member" && role !== "viewer") {
    return NextResponse.json(
      { error: "invalid_role", detail: "role must be admin, member, or viewer." },
      { status: 400 },
    );
  }
  try {
    return NextResponse.json(await gate.client.setMemberRole(userId, role));
  } catch (err) {
    return forwardMemberError(err);
  }
}
