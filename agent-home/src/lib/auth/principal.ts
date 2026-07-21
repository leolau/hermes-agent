/**
 * Principal resolution for the C1 bridge (FG-20 Wave A2).
 *
 * Small server-side helpers that sit between the signed `agent-home` session
 * (`session.ts`) and the two consumers of a principal: the typed Python-API
 * client and the server-side Supabase context. Keeping resolution here means a
 * route handler or RSC never re-derives "who is this request" ad hoc.
 */
import "server-only";

import { redirect } from "next/navigation";

import { HermesApiClient } from "@/lib/api/client";
import { readSession } from "@/lib/auth/session";
import type { Principal } from "@/types";

/** Return the current request's principal, or null if unauthenticated. */
export async function getPrincipal(): Promise<Principal | null> {
  const session = await readSession();
  return session?.principal ?? null;
}

/**
 * Return the current principal or redirect to `/login`. Use in RSC/route
 * handlers that require an authenticated principal.
 */
export async function requirePrincipal(): Promise<Principal> {
  const principal = await getPrincipal();
  if (!principal) {
    redirect("/login");
  }
  return principal;
}

/** A Python-API client bound to the current request's bridged token. */
export async function apiClientForRequest(): Promise<HermesApiClient> {
  const session = await readSession();
  return new HermesApiClient({ hermesToken: session?.hermesToken });
}

/**
 * Resolve a C1 principal from a freshly-obtained upstream Hermes token by
 * asking the Python API `whoami` (the authority on identity). Returns null
 * when the token doesn't map to an enrolled principal.
 */
export async function resolvePrincipalFromToken(
  hermesToken: string,
): Promise<Principal | null> {
  const client = new HermesApiClient({ hermesToken });
  const res = await client.whoami();
  return res.configured ? res.principal : null;
}
