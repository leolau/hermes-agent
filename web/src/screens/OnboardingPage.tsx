import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Circle, Copy } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type { OnboardingItem, OnboardingReadinessResponse } from "@/lib/api";

function ItemRow({ item }: { item: OnboardingItem }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    if (!item.fix_command) return;
    void navigator.clipboard?.writeText(item.fix_command).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [item.fix_command]);

  return (
    <li className="flex flex-col gap-2 p-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          {item.met ? (
            <CheckCircle2 className="h-5 w-5 text-emerald-500" />
          ) : (
            <Circle className="h-5 w-5 text-muted-foreground" />
          )}
          <span className="font-medium">{item.label}</span>
          <Badge tone={item.required ? "warning" : "secondary"}>
            {item.required ? "required" : "optional"}
          </Badge>
          <Badge tone={item.met ? "success" : "secondary"}>
            {item.met ? "ready" : "not set"}
          </Badge>
        </div>
        <span className="text-xs text-muted-foreground">{item.contract}</span>
      </div>
      <div className="text-sm text-muted-foreground">{item.rationale}</div>
      {item.detail ? (
        <div className="text-xs text-muted-foreground">{item.detail}</div>
      ) : null}
      {!item.met && item.fix_command ? (
        <div className="flex items-center gap-2">
          <code className="flex-1 rounded border border-border/60 bg-background/40 px-2 py-1 font-mono text-xs">
            {item.fix_command}
          </code>
          <Button ghost size="sm" onClick={copy}>
            <Copy className="h-4 w-4" /> {copied ? "Copied" : "Copy fix"}
          </Button>
        </div>
      ) : null}
    </li>
  );
}

/**
 * FG-17b onboarding first-run wizard. Consumes the FG-15 readiness API
 * (``GET /api/onboarding/readiness``) and renders the required/optional
 * setup items with their check status, fix command, and rationale, plus the
 * overall readiness score + ``ready_for_prod`` gate.
 */
export default function OnboardingPage() {
  const [readiness, setReadiness] =
    useState<OnboardingReadinessResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { setEnd } = usePageHeader();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReadiness(await api.getOnboardingReadiness());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load readiness");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setEnd(
      <Button ghost size="sm" onClick={() => void load()}>
        Re-check
      </Button>,
    );
    return () => setEnd(null);
  }, [setEnd, load]);

  if (loading) {
    return (
      <div
        data-component="OnboardingPage"
        className="flex items-center gap-2 text-muted-foreground"
      >
        <Spinner /> Checking setup readiness…
      </div>
    );
  }

  const required = (readiness?.items ?? []).filter((i) => i.required);
  const optional = (readiness?.items ?? []).filter((i) => !i.required);

  return (
    <div data-component="OnboardingPage" className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <H2>Getting started</H2>
      </div>

      {error ? (
        <Card>
          <CardContent className="p-5 text-sm text-destructive">
            {error}
          </CardContent>
        </Card>
      ) : null}

      {readiness ? (
        <Card>
          <CardContent className="p-5 flex flex-col gap-3">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <span className="text-3xl font-semibold">
                {readiness.score_pct}%
              </span>
              <Badge tone={readiness.ready_for_prod ? "success" : "warning"}>
                {readiness.ready_for_prod
                  ? "ready for prod"
                  : "setup incomplete"}
              </Badge>
            </div>
            <div className="h-2 w-full overflow-hidden rounded bg-border/60">
              <div
                className="h-full bg-emerald-500"
                style={{ width: `${readiness.score_pct}%` }}
              />
            </div>
            <div className="text-sm text-muted-foreground">
              Required: {readiness.required_met}/{readiness.required_total} ·
              Optional: {readiness.optional_met}/{readiness.optional_total}
            </div>
          </CardContent>
        </Card>
      ) : null}

      <div className="flex flex-col gap-3">
        <H2>Required</H2>
        <Card>
          <CardContent className="p-0">
            <ul className="divide-y divide-border">
              {required.map((item) => (
                <ItemRow key={item.key} item={item} />
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-col gap-3">
        <H2>Optional</H2>
        <Card>
          <CardContent className="p-0">
            <ul className="divide-y divide-border">
              {optional.map((item) => (
                <ItemRow key={item.key} item={item} />
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
