import { Pill } from "@/components/ui/Pill";
import type { OnboardingItem, OnboardingReadinessResponse } from "@/types";

/**
 * FG-20 Wave B3 — mobile-first onboarding wizard + readiness (read-only, C7).
 *
 * The mobile face of `web/`'s `OnboardingPage`: it consumes the FG-15 readiness
 * API (`/api/onboarding/readiness`) and renders the overall score +
 * `ready_for_prod` gate, then the required/optional setup checks with their
 * status, rationale, and `hermes …` fix command. It reports secret *presence*
 * only (never values) and never mutates config — fixes run on the CLI.
 */

function ItemRow({ item }: { item: OnboardingItem }) {
  return (
    <li data-component="OnboardingItemRow">
      <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span aria-hidden>{item.met ? "✓" : "○"}</span>
          <span className="font-medium">{item.label}</span>
          <Pill tone={item.required ? "warning" : "muted"}>
            {item.required ? "required" : "optional"}
          </Pill>
          <Pill tone={item.met ? "success" : "muted"}>
            {item.met ? "ready" : "not set"}
          </Pill>
        </div>
        <p className="mt-2 text-sm text-[var(--color-muted)]">{item.rationale}</p>
        {item.detail ? (
          <p className="mt-1 text-xs text-[var(--color-muted)]">{item.detail}</p>
        ) : null}
        {!item.met && item.fix_command ? (
          <code className="mt-2 block overflow-x-auto rounded-lg bg-[var(--color-surface-2)] px-2 py-1 font-mono text-xs">
            {item.fix_command}
          </code>
        ) : null}
      </div>
    </li>
  );
}

export function OnboardingWizard({
  readiness,
}: {
  readiness: OnboardingReadinessResponse;
}) {
  const required = readiness.items.filter((i) => i.required);
  const optional = readiness.items.filter((i) => !i.required);

  return (
    <div data-component="OnboardingWizard" className="flex flex-col gap-4">
      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="flex items-center justify-between gap-3">
          <span className="text-3xl font-semibold">{readiness.score_pct}%</span>
          <Pill tone={readiness.ready_for_prod ? "success" : "warning"}>
            {readiness.ready_for_prod ? "ready for prod" : "setup incomplete"}
          </Pill>
        </div>
        <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-[var(--color-surface-2)]">
          <div
            className="h-full bg-emerald-500"
            style={{ width: `${readiness.score_pct}%` }}
          />
        </div>
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Required: {readiness.required_met}/{readiness.required_total} · Optional:{" "}
          {readiness.optional_met}/{readiness.optional_total}
        </p>
      </section>

      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
          Required
        </h2>
        {required.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">No required items.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {required.map((item) => (
              <ItemRow key={item.key} item={item} />
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
          Optional
        </h2>
        {optional.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">No optional items.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {optional.map((item) => (
              <ItemRow key={item.key} item={item} />
            ))}
          </ul>
        )}
      </section>

      <p className="text-xs text-[var(--color-muted)]">
        Read-only · fixes run on the CLI (`hermes …`). Secret presence only —
        values are never shown.
      </p>
    </div>
  );
}
