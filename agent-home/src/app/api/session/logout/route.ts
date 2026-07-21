/**
 * POST /api/session/logout — clear the `agent-home` session cookie.
 *
 * Best-effort revoke of the upstream Hermes session is left to the Python
 * API's own `/auth/logout`; here we simply drop the local bridge cookie so the
 * browser is logged out of `agent-home`.
 */
import { NextResponse } from "next/server";

import { clearSession } from "@/lib/auth/session";

export async function POST(): Promise<NextResponse> {
  await clearSession();
  return NextResponse.json({ ok: true });
}
