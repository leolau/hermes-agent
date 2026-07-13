import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ShieldCheck, FileWarning, Lock } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type {
  CommsChange,
  CommsTraceSummary,
  CoreManifestResponse,
} from "@/lib/api";

/**
 * FG-17b Core-area view: a permanent, read-only window on the C7 Core
 * boundary. It surfaces the FG-14 ``core_manifest.yaml`` globs + boundary
 * health, the FG-12 change log, and the FG-16 interaction trace. Core is
 * immutable to the runtime agent and is changed only by humans through the
 * repo/PR flow — so nothing here mutates state.
 */
export default function CorePage() {
  const [manifest, setManifest] = useState<CoreManifestResponse | null>(null);
  const [changes, setChanges] = useState<CommsChange[]>([]);
  const [traces, setTraces] = useState<CommsTraceSummary[]>([]);
  const [changesConfigured, setChangesConfigured] = useState(true);
  const [tracesConfigured, setTracesConfigured] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { setEnd } = usePageHeader();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [manifestResp, changesResp, tracesResp] = await Promise.all([
        api.getCoreManifest(),
        api
          .getCommsChanges()
          .catch(() => ({ configured: false, changes: [] as CommsChange[] })),
        api
          .getCommsTraces({ limit: 50 })
          .catch(() => ({
            configured: false,
            traces: [] as CommsTraceSummary[],
          })),
      ]);
      setManifest(manifestResp);
      setChangesConfigured(changesResp.configured);
      setChanges(changesResp.changes ?? []);
      setTracesConfigured(tracesResp.configured);
      setTraces(tracesResp.traces ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load Core view");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setEnd(<Badge tone="secondary">read-only · Core (C7)</Badge>);
    return () => setEnd(null);
  }, [setEnd]);

  if (loading) {
    return (
      <div
        data-component="CorePage"
        className="flex items-center gap-2 text-muted-foreground"
      >
        <Spinner /> Loading Core boundary…
      </div>
    );
  }

  return (
    <div data-component="CorePage" className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <H2>Core area</H2>
      </div>

      {error ? (
        <Card>
          <CardContent className="p-5 text-sm text-destructive">
            {error}
          </CardContent>
        </Card>
      ) : null}

      {/* Boundary health */}
      {manifest ? (
        <Card>
          <CardContent className="p-5 flex flex-col gap-3">
            <div className="flex items-center gap-2">
              {manifest.fallback_active ? (
                <FileWarning className="h-5 w-5 text-amber-500" />
              ) : (
                <ShieldCheck className="h-5 w-5 text-emerald-500" />
              )}
              <span className="font-medium">Core boundary</span>
              <Badge tone={manifest.fallback_active ? "warning" : "success"}>
                {manifest.fallback_active
                  ? "fail-closed fallback"
                  : "manifest active"}
              </Badge>
              {manifest.self_protected ? (
                <Badge tone="secondary">
                  <Lock className="mr-1 h-3 w-3" /> self-protected
                </Badge>
              ) : null}
            </div>
            <div className="text-sm text-muted-foreground">
              <code>{manifest.manifest_path}</code> defines{" "}
              <strong>{manifest.glob_count}</strong> Core globs. The runtime
              agent can never write these paths; Core changes only through the
              human repo/PR flow.
            </div>
            <ul className="grid grid-cols-1 gap-1 sm:grid-cols-2">
              {manifest.globs.map((glob) => (
                <li
                  key={glob}
                  className="rounded border border-border/60 bg-background/40 px-2 py-1 font-mono text-xs"
                >
                  {glob}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      {/* Core-denial audit */}
      <div className="flex flex-col gap-3">
        <H2>Boundary denials</H2>
        {manifest && manifest.denials.length > 0 ? (
          <Card>
            <CardContent className="p-0">
              <ul className="divide-y divide-border">
                {manifest.denials.map((denial) => (
                  <li key={denial.id} className="flex flex-col gap-1 p-3">
                    <div className="flex items-center gap-2">
                      <AlertTriangle className="h-4 w-4 text-amber-500" />
                      <Badge tone="secondary">{denial.mode}</Badge>
                      <span className="text-sm text-muted-foreground">
                        {denial.actor_user_id}
                      </span>
                    </div>
                    <span className="text-sm">{denial.summary}</span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        ) : (
          <p className="text-sm text-muted-foreground">
            No Core-write denials recorded — the agent has not attempted to
            cross the boundary.
          </p>
        )}
      </div>

      {/* FG-12 change log */}
      <div className="flex flex-col gap-3">
        <H2>Change log (FG-12)</H2>
        {!changesConfigured ? (
          <p className="text-sm text-muted-foreground">
            Change log not configured (needs the application datastore).
          </p>
        ) : changes.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No changes visible in your scope.
          </p>
        ) : (
          <Card>
            <CardContent className="p-0">
              <ul className="divide-y divide-border">
                {changes.map((change) => (
                  <li
                    key={change.id}
                    className="flex items-center justify-between gap-3 p-3 text-sm"
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge tone="secondary">{change.mode}</Badge>
                      <Badge tone="secondary">{change.target_kind}</Badge>
                      <span className="text-muted-foreground">
                        {change.actor_user_id ?? "—"}
                      </span>
                      {change.reversible ? (
                        <Badge tone="secondary">reversible</Badge>
                      ) : null}
                      {change.undone ? (
                        <Badge tone="warning">undone</Badge>
                      ) : null}
                    </div>
                    <code className="text-xs text-muted-foreground">
                      {change.id.slice(0, 16)}
                    </code>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>

      {/* FG-16 interaction trace */}
      <div className="flex flex-col gap-3">
        <H2>Interaction trace (FG-16)</H2>
        {!tracesConfigured ? (
          <p className="text-sm text-muted-foreground">
            Trace ledger not configured (needs the application datastore).
          </p>
        ) : traces.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No traces visible in your scope.
          </p>
        ) : (
          <Card>
            <CardContent className="p-0">
              <ul className="divide-y divide-border">
                {traces.map((trace) => (
                  <li
                    key={trace.trace_id}
                    className="flex items-center justify-between gap-3 p-3 text-sm"
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      {trace.platform ? (
                        <Badge tone="secondary">{trace.platform}</Badge>
                      ) : null}
                      <Badge tone="secondary">{trace.event_count} events</Badge>
                      <span className="text-muted-foreground">
                        {trace.actor_user_id ?? "—"}
                      </span>
                    </div>
                    <code className="text-xs text-muted-foreground">
                      {trace.trace_id.slice(0, 20)}
                    </code>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
