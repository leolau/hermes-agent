import { useCallback, useEffect, useState } from "react";
import { Globe, Lock, ShieldAlert, ShieldCheck } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Switch } from "@nous-research/ui/ui/components/switch";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type { WebviewActionResponse, WebviewSession } from "@/lib/api";
import { decisionTone, parseAllowedDomains } from "@/lib/webview";

const ACTION_KINDS = [
  "navigate",
  "read",
  "screenshot",
  "scroll",
  "click",
  "type",
  "select",
  "submit",
  "download",
] as const;

/**
 * FG-17b Agent webview: the consent surface for the CDP-backed agent browser.
 * Default-deny — nothing runs until the operator opens a session with an
 * explicit scope (allowed domains + read-only/interactive). In-scope reads run
 * autonomously; off-scope / interactive-under-read-only / credentialed /
 * destructive actions escalate to a per-action approval (C6). Every decision
 * is traced (C8) under the session's ``trace_id``; the browser profile is
 * isolated per user. This panel drives the policy — it never bypasses it.
 */
export default function WebviewPage() {
  const [session, setSession] = useState<WebviewSession | null>(null);
  const [configured, setConfigured] = useState(true);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { setEnd } = usePageHeader();

  // Consent form.
  const [domains, setDomains] = useState("");
  const [interactive, setInteractive] = useState(false);

  // Action console.
  const [kind, setKind] = useState<string>("navigate");
  const [url, setUrl] = useState("");
  const [credentialed, setCredentialed] = useState(false);
  const [destructive, setDestructive] = useState(false);
  const [lastResult, setLastResult] = useState<WebviewActionResponse | null>(
    null,
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.getWebviewSession();
      setConfigured(resp.configured);
      setSession(resp.session);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load webview");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setEnd(<Badge tone="secondary">consent-gated (C6) · traced (C8)</Badge>);
    return () => setEnd(null);
  }, [setEnd]);

  const openSession = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const resp = await api.openWebviewSession({
        allowed_domains: parseAllowedDomains(domains),
        mode: interactive ? "interactive" : "read_only",
      });
      setSession(resp.session);
      setLastResult(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open session");
    } finally {
      setBusy(false);
    }
  }, [domains, interactive]);

  const closeSession = useCallback(async () => {
    setBusy(true);
    try {
      await api.closeWebviewSession();
      setSession(null);
      setLastResult(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to close session");
    } finally {
      setBusy(false);
    }
  }, []);

  const requestAction = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const resp = await api.requestWebviewAction({
        kind,
        url: url.trim() || null,
        credentialed,
        destructive,
      });
      setLastResult(resp);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action was refused");
    } finally {
      setBusy(false);
    }
  }, [kind, url, credentialed, destructive, load]);

  const resolveApproval = useCallback(
    async (approvalId: string, grant: boolean) => {
      setBusy(true);
      try {
        const resp = await api.resolveWebviewApproval(approvalId, grant);
        setLastResult(resp);
        await load();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to resolve");
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  if (loading) {
    return (
      <div
        data-component="WebviewPage"
        className="flex items-center gap-2 text-muted-foreground"
      >
        <Spinner /> Loading webview…
      </div>
    );
  }

  return (
    <div data-component="WebviewPage" className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <H2>Agent webview</H2>
      </div>

      {!configured ? (
        <Card>
          <CardContent className="p-5 text-sm text-muted-foreground">
            Webview needs the multi-user datastore configured.
          </CardContent>
        </Card>
      ) : null}

      {error ? (
        <Card>
          <CardContent className="p-5 text-sm text-destructive">
            {error}
          </CardContent>
        </Card>
      ) : null}

      {session === null ? (
        /* Default-deny: no session, so the agent cannot touch the browser. */
        <Card>
          <CardContent className="flex flex-col gap-4 p-5">
            <div className="flex items-center gap-2">
              <Lock className="h-5 w-5 text-muted-foreground" />
              <span className="font-medium">No session (default-deny)</span>
            </div>
            <p className="text-sm text-muted-foreground">
              The agent cannot drive the browser until you opt in with an
              explicit consent scope. In-scope reads run autonomously;
              everything else escalates for your approval.
            </p>
            <div className="flex flex-col gap-2">
              <Label htmlFor="wv-domains">Allowed domains (comma-separated)</Label>
              <Input
                id="wv-domains"
                placeholder="example.com, docs.internal"
                value={domains}
                onChange={(e) => setDomains(e.target.value)}
              />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={interactive} onCheckedChange={setInteractive} />
              Allow interactive actions (click / type / select) in scope
            </label>
            <Button
              className="w-fit uppercase"
              disabled={busy}
              onClick={() => void openSession()}
            >
              Open session
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Active session scope */}
          <Card>
            <CardContent className="flex flex-col gap-3 p-5">
              <div className="flex flex-wrap items-center gap-2">
                <ShieldCheck className="h-5 w-5 text-emerald-500" />
                <span className="font-medium">Session open</span>
                <Badge tone={session.scope.mode === "interactive" ? "warning" : "success"}>
                  {session.scope.mode}
                </Badge>
                <Badge tone="secondary">
                  {session.scope.allowed_domains.length} domain(s)
                </Badge>
                <code className="text-xs text-muted-foreground">
                  trace {session.trace_id.slice(0, 16)}
                </code>
              </div>
              <ul className="flex flex-wrap gap-1">
                {session.scope.allowed_domains.length === 0 ? (
                  <li className="text-sm text-muted-foreground">
                    No domains in scope — every navigation will escalate.
                  </li>
                ) : (
                  session.scope.allowed_domains.map((d) => (
                    <li
                      key={d}
                      className="rounded border border-border/60 bg-background/40 px-2 py-1 font-mono text-xs"
                    >
                      {d}
                    </li>
                  ))
                )}
              </ul>
              <Button
                ghost
                className="w-fit uppercase"
                disabled={busy}
                onClick={() => void closeSession()}
              >
                Close session
              </Button>
            </CardContent>
          </Card>

          {/* Action console */}
          <Card>
            <CardContent className="flex flex-col gap-3 p-5">
              <div className="flex items-center gap-2">
                <Globe className="h-5 w-5 text-muted-foreground" />
                <span className="font-medium">Request an action</span>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-2">
                  <Label htmlFor="wv-kind">Kind</Label>
                  <Select id="wv-kind" value={kind} onValueChange={setKind}>
                    {ACTION_KINDS.map((k) => (
                      <SelectOption key={k} value={k}>
                        {k}
                      </SelectOption>
                    ))}
                  </Select>
                </div>
                <div className="flex flex-col gap-2">
                  <Label htmlFor="wv-url">URL (optional)</Label>
                  <Input
                    id="wv-url"
                    placeholder="https://example.com/page"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                  />
                </div>
              </div>
              <div className="flex flex-wrap gap-4">
                <label className="flex items-center gap-2 text-sm">
                  <Switch
                    checked={credentialed}
                    onCheckedChange={setCredentialed}
                  />
                  Credentialed (login / secret entry)
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Switch
                    checked={destructive}
                    onCheckedChange={setDestructive}
                  />
                  Destructive (purchase / delete / submit)
                </label>
              </div>
              <Button
                className="w-fit uppercase"
                disabled={busy}
                onClick={() => void requestAction()}
              >
                Request action
              </Button>

              {lastResult ? (
                <div className="flex items-center gap-2 text-sm">
                  <Badge tone={decisionTone(lastResult.decision)}>
                    {lastResult.decision}
                  </Badge>
                  <span className="text-muted-foreground">
                    {lastResult.reason}
                    {lastResult.detail ? ` — ${lastResult.detail}` : ""}
                  </span>
                </div>
              ) : null}
            </CardContent>
          </Card>

          {/* Pending approvals (C6) */}
          <div className="flex flex-col gap-3">
            <H2>Pending approvals (C6)</H2>
            {session.pending.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No actions are waiting for approval.
              </p>
            ) : (
              <Card>
                <CardContent className="p-0">
                  <ul className="divide-y divide-border">
                    {session.pending.map((p) => (
                      <li
                        key={p.id}
                        className="flex items-center justify-between gap-3 p-3"
                      >
                        <div className="flex flex-col gap-1">
                          <div className="flex items-center gap-2">
                            <ShieldAlert className="h-4 w-4 text-amber-500" />
                            <Badge tone="secondary">{p.kind}</Badge>
                            {p.url ? (
                              <code className="text-xs text-muted-foreground">
                                {p.url}
                              </code>
                            ) : null}
                          </div>
                          <span className="text-sm text-muted-foreground">
                            {p.reason}
                          </span>
                        </div>
                        <div className="flex gap-2">
                          <Button
                            className="uppercase"
                            disabled={busy}
                            onClick={() => void resolveApproval(p.id, true)}
                          >
                            Approve
                          </Button>
                          <Button
                            ghost
                            className="uppercase"
                            disabled={busy}
                            onClick={() => void resolveApproval(p.id, false)}
                          >
                            Deny
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}
          </div>
        </>
      )}
    </div>
  );
}
