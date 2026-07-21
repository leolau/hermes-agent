import { MobileShell } from "@/components/MobileShell";
import { OnboardingWizard } from "@/components/onboarding/OnboardingWizard";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { OnboardingReadinessResponse } from "@/types";

export const dynamic = "force-dynamic";

/**
 * FG-20 Wave B3 — onboarding wizard + readiness. BFF: resolves the principal
 * and reads the FG-15 readiness projection (`/api/onboarding/readiness`).
 * Read-only — fixes run on the CLI. Reached from Home (no bottom-nav slot).
 */
export default async function Page() {
  await requirePrincipal();

  let readiness: OnboardingReadinessResponse | null = null;
  let error: string | null = null;
  try {
    const client = await apiClientForRequest();
    readiness = await client.onboardingReadiness();
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load readiness";
  }

  return (
    <MobileShell title="Getting started">
      {error || !readiness ? (
        <div
          data-component="OnboardingError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load setup readiness{error ? ` (${error})` : ""}.
        </div>
      ) : (
        <OnboardingWizard readiness={readiness} />
      )}
    </MobileShell>
  );
}
