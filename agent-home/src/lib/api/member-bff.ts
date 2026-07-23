/**
 * Shared server-side helpers for the member-management BFF routes (PR-4).
 *
 * The owner/admin gate and the upstream-error mapping are identical across the
 * five `/api/comms/members[...]` handlers, so they live here. This is a
 * *server-side* authorization check for a clean UX — the Python layer enforces
 * the same guard independently as the authority. No browser code and no
 * service-role key ever touch this path.
 */
import "server-only";

import { NextResponse } from "next/server";

import { HermesApiClient, HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

/**
 * Resolve an owner/admin API client for the request, or a `NextResponse`
 * carrying the right status (401 unauthenticated, 403 not owner/admin).
 * Callers branch on `"client" in gate`.
 */
export async function requireMemberAdmin(): Promise<
  { client: HermesApiClient } | { response: NextResponse }
> {
  const principal = await getPrincipal();
  if (!principal) {
    return { response: NextResponse.json({ error: "unauthenticated" }, { status: 401 }) };
  }
  if (principal.role !== "owner" && principal.role !== "admin") {
    return { response: NextResponse.json({ error: "forbidden" }, { status: 403 }) };
  }
  return { client: await apiClientForRequest() };
}

/** Map an upstream failure onto the BFF's error envelope + status. */
export function forwardMemberError(err: unknown): NextResponse {
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
