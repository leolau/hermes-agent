/**
 * POST /api/session/login — the C1 principal bridge login (FG-20 Wave A2).
 *
 * Flow (Decision 1 = BRIDGE):
 *   1. Forward the submitted credentials to the Python API's existing
 *      `dashboard_auth` password-login (`POST /auth/password-login`).
 *   2. Capture the upstream `hermes_session_at` token from its `Set-Cookie`.
 *   3. Resolve the C1 principal for that token (`/api/comms/whoami`).
 *   4. Mint the signed `agent-home` session cookie carrying the token +
 *      principal snapshot.
 *
 * `agent-home` never verifies the password itself — the Python
 * `dashboard_auth` provider remains the single credential authority.
 */
import { NextResponse } from "next/server";

import { hermesApiBaseUrl } from "@/lib/env";
import { resolvePrincipalFromToken } from "@/lib/auth/principal";
import { writeSession } from "@/lib/auth/session";

interface LoginBody {
  provider?: unknown;
  username?: unknown;
  password?: unknown;
}

/** Pull the upstream `hermes_session_at` value out of Set-Cookie headers. */
function extractHermesToken(setCookies: string[]): string | null {
  for (const raw of setCookies) {
    // Cookie name may be bare or carry a `__Host-` / `__Secure-` prefix.
    const match = raw.match(/(?:^|;\s*)(?:__Host-|__Secure-)?hermes_session_at=([^;]+)/);
    if (match) {
      return decodeURIComponent(match[1]);
    }
  }
  return null;
}

export async function POST(request: Request): Promise<NextResponse> {
  let body: LoginBody;
  try {
    body = (await request.json()) as LoginBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const provider = typeof body.provider === "string" ? body.provider : "";
  const username = typeof body.username === "string" ? body.username : "";
  const password = typeof body.password === "string" ? body.password : "";
  if (!provider || !username || !password) {
    return NextResponse.json(
      { error: "missing_fields", detail: "provider, username and password are required" },
      { status: 400 },
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${hermesApiBaseUrl()}/auth/password-login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ provider, username, password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { error: "api_unreachable", detail: "The AI layer could not be reached." },
      { status: 502 },
    );
  }

  if (!upstream.ok) {
    // Mirror the Python endpoint's deliberately generic failures.
    const status = upstream.status === 401 ? 401 : upstream.status;
    return NextResponse.json(
      { error: "login_failed", detail: "Invalid credentials or provider." },
      { status },
    );
  }

  const token = extractHermesToken(upstream.headers.getSetCookie());
  if (!token) {
    return NextResponse.json(
      { error: "no_session", detail: "Upstream login returned no session token." },
      { status: 502 },
    );
  }

  const principal = await resolvePrincipalFromToken(token);
  if (!principal) {
    return NextResponse.json(
      {
        error: "no_principal",
        detail: "Authenticated, but no C1 principal is enrolled for this user.",
      },
      { status: 409 },
    );
  }

  await writeSession({
    hermesToken: token,
    principal,
    issuedAt: Math.floor(Date.now() / 1000),
  });
  return NextResponse.json({ ok: true, principal });
}
