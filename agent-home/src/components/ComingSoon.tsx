/**
 * Placeholder body for tab destinations whose real feature panel lands in a
 * later wave. Keeps the shell navigable without pretending to be a feature.
 */
export function ComingSoon({ wave, feature }: { wave: string; feature: string }) {
  return (
    <div
      data-component="ComingSoon"
      className="rounded-2xl border border-dashed border-[var(--color-border)] p-6 text-center"
    >
      <p className="text-sm font-medium">{feature}</p>
      <p className="mt-1 text-xs text-[var(--color-muted)]">
        Lands in {wave}. Wave A ships the shell + auth/data seam only.
      </p>
    </div>
  );
}
