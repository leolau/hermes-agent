/**
 * BFF for member management (FG-20 PR-4 / multi-user item e — frontend).
 *
 * - `GET`  → list enrolled members (joined with GoTrue account state).
 * - `POST` → create a Supabase account + enrol it as a principal.
 *
 * Both forward to the Python API `/api/comms/members` under the bridged C1
 * principal. Authorization is owner/admin-only and is enforced **twice**: this
 * server-side route rejects a non-admin principal early (clean UX), and the
 * Python layer enforces it independently as the authority. The browser never
 * calls GoTrue and never holds the service-role key.
 */
import { NextResponse } from "next/server";

import { forwardMemberError, requireMemberAdmin } from "@/lib/api/member-bff";

interface CreateBody {
  email?: unknown;
  password?: unknown;
  display?: unknown;
  role?: unknown;
}

export async function GET(): Promise<NextResponse> {
  const gate = await requireMemberAdmin();
  if ("response" in gate) return gate.response;
  try {
    return NextResponse.json(await gate.client.members());
  } catch (err) {
    return forwardMemberError(err);
  }
}

export async function POST(request: Request): Promise<NextResponse> {
  const gate = await requireMemberAdmin();
  if ("response" in gate) return gate.response;
  let body: CreateBody;
  try {
    body = (await request.json()) as CreateBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const email = typeof body.email === "string" ? body.email.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  const display = typeof body.display === "string" ? body.display.trim() : "";
  const role = typeof body.role === "string" ? body.role.trim() : "member";
  if (!email || !password) {
    return NextResponse.json(
      { error: "invalid_input", detail: "email and password are required." },
      { status: 400 },
    );
  }
  if (role !== "admin" && role !== "member" && role !== "viewer") {
    return NextResponse.json(
      { error: "invalid_role", detail: "role must be admin, member, or viewer." },
      { status: 400 },
    );
  }
  try {
    return NextResponse.json(
      await gate.client.createMember({ email, password, display, role }),
    );
  } catch (err) {
    return forwardMemberError(err);
  }
}
