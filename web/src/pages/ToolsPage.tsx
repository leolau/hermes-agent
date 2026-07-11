import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Power, Rocket, Settings, Stethoscope } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Toast } from "@nous-research/ui/ui/components/toast";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@nous-research/ui/ui/components/dialog";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type { Tool, ToolChange, ToolHealth, ToolMode } from "@/lib/api";

const STATUS_TONE: Record<string, "success" | "secondary"> = {
  enabled: "success",
  disabled: "secondary",
};

const KIND_TONE: Record<string, "success" | "warning" | "secondary"> = {
  in_house: "success",
  remote: "warning",
  builtin: "secondary",
};

export default function ToolsPage() {
  const [mode, setMode] = useState<ToolMode>("dev");
  const [tools, setTools] = useState<Tool[]>([]);
  const [changes, setChanges] = useState<ToolChange[]>([]);
  const [configured, setConfigured] = useState(true);
  const [detail, setDetail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [health, setHealth] = useState<Record<string, ToolHealth>>({});
  const [configTool, setConfigTool] = useState<Tool | null>(null);
  const [configText, setConfigText] = useState("");
  const [configError, setConfigError] = useState<string | null>(null);
  const [savingConfig, setSavingConfig] = useState(false);
  const { toast, showToast } = useToast();
  const { setEnd } = usePageHeader();

  const load = useCallback(() => {
    return Promise.all([
      api.getTools(mode),
      api
        .getToolChanges(mode)
        .catch(() => ({ configured: true, changes: [] as ToolChange[] })),
    ])
      .then(([toolsResp, changesResp]) => {
        setConfigured(toolsResp.configured);
        setDetail(toolsResp.detail ?? null);
        setTools(toolsResp.tools);
        setChanges(changesResp.changes ?? []);
      })
      .catch((err) =>
        showToast(
          err instanceof Error ? err.message : "Failed to load tools",
          "error",
        ),
      );
  }, [mode, showToast]);

  useEffect(() => {
    load().finally(() => setLoading(false));
  }, [load]);

  useEffect(() => {
    setEnd(
      <Select
        value={mode}
        onValueChange={(v) => {
          setLoading(true);
          setMode(v as ToolMode);
        }}
        aria-label="Datastore mode"
      >
        <SelectOption value="dev">dev</SelectOption>
        <SelectOption value="prod">prod</SelectOption>
      </Select>,
    );
    return () => setEnd(null);
  }, [mode, setEnd]);

  const toggleEnabled = useCallback(
    async (tool: Tool) => {
      setBusy(tool.name);
      try {
        await api.setToolEnabled(tool.name, !tool.enabled, mode);
        showToast(
          `${tool.name} ${tool.enabled ? "disabled" : "enabled"}`,
          "success",
        );
        await load();
      } catch (err) {
        showToast(err instanceof Error ? err.message : "Toggle failed", "error");
      } finally {
        setBusy(null);
      }
    },
    [mode, load, showToast],
  );

  const promote = useCallback(
    async (tool: Tool) => {
      setBusy(tool.name);
      try {
        const result = await api.promoteTool(tool.name, mode);
        showToast(
          `Promoted ${tool.name} dev→prod (change ${result.change_ref.slice(0, 12)})`,
          "success",
        );
        await load();
      } catch (err) {
        showToast(err instanceof Error ? err.message : "Promotion failed", "error");
      } finally {
        setBusy(null);
      }
    },
    [mode, load, showToast],
  );

  const openConfig = useCallback((tool: Tool) => {
    setConfigTool(tool);
    setConfigError(null);
    setConfigText(JSON.stringify(tool.config_json ?? {}, null, 2));
  }, []);

  const saveConfig = useCallback(async () => {
    if (!configTool) return;
    let parsed: Record<string, unknown>;
    try {
      const raw = configText.trim() === "" ? {} : JSON.parse(configText);
      if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
        throw new Error("Config must be a JSON object");
      }
      parsed = raw as Record<string, unknown>;
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "Invalid JSON");
      return;
    }
    setSavingConfig(true);
    setConfigError(null);
    try {
      await api.setToolConfig(configTool.name, parsed, mode);
      showToast(`${configTool.name} config saved`, "success");
      setConfigTool(null);
      await load();
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSavingConfig(false);
    }
  }, [configTool, configText, mode, load, showToast]);

  const checkHealth = useCallback(
    async (tool: Tool) => {
      setBusy(tool.name);
      try {
        const result = await api.getToolHealth(tool.name, mode);
        setHealth((prev) => ({ ...prev, [tool.name]: result }));
        showToast(
          `${tool.name}: ${result.reachable ? "reachable" : "unreachable"}`,
          result.reachable ? "success" : "error",
        );
      } catch (err) {
        showToast(err instanceof Error ? err.message : "Health check failed", "error");
      } finally {
        setBusy(null);
      }
    },
    [mode, showToast],
  );

  return (
    <div data-component="ToolsPage" className="flex flex-col gap-6">
      <Toast toast={toast} />

      <div className="flex items-center justify-between">
        <H2>Tools</H2>
      </div>

      {!configured && (
        <Card>
          <CardContent className="p-5 text-sm text-muted-foreground">
            The application datastore isn't configured, so no tools can be
            listed. Set <code>datastore.supabase_app.dsn</code> in your
            config.yaml, then scaffold one with{" "}
            <code>hermes tool new &lt;name&gt;</code>.
            {detail ? <div className="mt-2 opacity-70">{detail}</div> : null}
          </CardContent>
        </Card>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Spinner /> Loading tools…
        </div>
      ) : configured && tools.length === 0 ? (
        <Card>
          <CardContent className="p-5 text-sm text-muted-foreground">
            No tools visible in <strong>{mode}</strong>. Create one with{" "}
            <code>hermes tool new &lt;name&gt;</code>.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {tools.map((tool) => (
            <Card key={tool.id}>
              <CardContent className="p-4 flex flex-col gap-3">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium">{tool.name}</span>
                    <Badge tone={KIND_TONE[tool.kind] ?? "secondary"}>
                      {tool.kind}
                    </Badge>
                    <Badge tone={STATUS_TONE[tool.status] ?? "secondary"}>
                      {tool.status}
                    </Badge>
                    <Badge tone="secondary">{tool.mode}</Badge>
                    <Badge tone="secondary">{tool.visibility}</Badge>
                    {health[tool.name] ? (
                      <Badge
                        tone={health[tool.name].reachable ? "success" : "secondary"}
                      >
                        {health[tool.name].reachable ? "up" : "down"}
                      </Badge>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2">
                    {tool.web_url ? (
                      <a
                        href={tool.web_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                      >
                        <ExternalLink className="h-4 w-4" /> Web UI
                      </a>
                    ) : null}
                    <Button
                      ghost
                      size="sm"
                      disabled={busy === tool.name}
                      onClick={() => void checkHealth(tool)}
                    >
                      <Stethoscope className="h-4 w-4" /> Health
                    </Button>
                    <Button
                      ghost
                      size="sm"
                      disabled={busy === tool.name}
                      onClick={() => openConfig(tool)}
                    >
                      <Settings className="h-4 w-4" /> Config
                    </Button>
                    <Button
                      size="sm"
                      disabled={busy === tool.name}
                      onClick={() => void toggleEnabled(tool)}
                    >
                      <Power className="h-4 w-4" />
                      {tool.enabled ? "Disable" : "Enable"}
                    </Button>
                    {tool.mode === "dev" ? (
                      <Button
                        size="sm"
                        disabled={busy === tool.name}
                        onClick={() => void promote(tool)}
                      >
                        <Rocket className="h-4 w-4" /> Promote
                      </Button>
                    ) : null}
                  </div>
                </div>
                {tool.mcp_endpoint_ref ? (
                  <div className="text-xs text-muted-foreground">
                    MCP endpoint: <code>{tool.mcp_endpoint_ref}</code> · stack:{" "}
                    <code>{tool.stack || "—"}</code>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog
        open={configTool !== null}
        onOpenChange={(o) => !o && setConfigTool(null)}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Configure {configTool?.name}</DialogTitle>
            <DialogDescription>
              Behavioural config stored as <code>config_json</code> in the{" "}
              <strong>{mode}</strong> registry. Must be a JSON object; keys
              matching <code>HERMES_*</code> are rejected (secrets belong in the
              tool's own <code>.env</code>).
            </DialogDescription>
          </DialogHeader>
          <textarea
            spellCheck={false}
            aria-label="Tool config JSON"
            className="min-h-[240px] max-h-[50vh] w-full resize-y border border-border bg-background/40 px-3 py-2 font-mono text-xs leading-relaxed shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/30"
            value={configText}
            onChange={(e) => setConfigText(e.target.value)}
          />
          {configError ? (
            <p className="whitespace-pre-wrap text-xs text-destructive">
              {configError}
            </p>
          ) : null}
          <div className="flex items-center justify-end gap-2">
            <Button
              ghost
              size="sm"
              disabled={savingConfig}
              onClick={() => setConfigTool(null)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={savingConfig}
              prefix={savingConfig ? <Spinner /> : undefined}
              onClick={() => void saveConfig()}
            >
              {savingConfig ? "Saving…" : "Save config"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <div className="flex flex-col gap-3">
        <H2>Change log</H2>
        {changes.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No recorded changes visible.
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
                      {change.undone ? <Badge tone="warning">undone</Badge> : null}
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
    </div>
  );
}
