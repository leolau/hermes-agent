import { useCallback, useEffect, useState } from "react";
import {
  Bell,
  Check,
  Target,
  History,
  Brain,
  RotateCcw,
  RotateCw,
  X,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Segmented } from "@nous-research/ui/ui/components/segmented";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { api } from "@/lib/api";
import type {
  CommsChange,
  CommsGoal,
  CommsMemory,
  CommsNotification,
  CommsPrincipal,
} from "@/lib/api";

type Section = "approvals" | "goals" | "changes" | "memory";

function NotConfiguredCard() {
  return (
    <Card data-component="NotConfiguredCard">
      <CardContent className="py-8 text-center text-sm text-muted-foreground">
        The multi-user application datastore is not configured on this
        instance. Set <code>datastore.supabase_app.dsn</code> in
        <code> config.yaml</code> to enable human-comms parity.
      </CardContent>
    </Card>
  );
}

function EmptyCard({ label }: { label: string }) {
  return (
    <Card data-component="EmptyCard">
      <CardContent className="py-8 text-center text-sm text-muted-foreground">
        {label}
      </CardContent>
    </Card>
  );
}

function PrincipalBanner({ principal }: { principal: CommsPrincipal | null }) {
  if (!principal) return null;
  return (
    <div
      data-component="PrincipalBanner"
      className="flex items-center gap-2 text-xs text-muted-foreground"
    >
      <span>Viewing as</span>
      <Badge tone="outline">{principal.display || principal.user_id}</Badge>
      <Badge tone="outline">{principal.role}</Badge>
    </div>
  );
}

function ApprovalsSection({
  onToast,
}: {
  onToast: (msg: string, kind: "success" | "error") => void;
}) {
  const [items, setItems] = useState<CommsNotification[]>([]);
  const [configured, setConfigured] = useState(true);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .getCommsNotifications()
      .then((res) => {
        setConfigured(res.configured);
        setItems(res.notifications);
      })
      .catch(() => onToast("Failed to load pending items", "error"))
      .finally(() => setLoading(false));
  }, [onToast]);

  useEffect(() => {
    load();
  }, [load]);

  const answer = async (item: CommsNotification, value: string) => {
    setBusy(item.id);
    try {
      const res = await api.answerCommsNotification(item.id, value);
      // Cross-surface dedupe: if Telegram already answered, the item was
      // already cleared — say so rather than implying this click decided it.
      onToast(
        res.newly_answered
          ? `Answered "${item.title}" (${value})`
          : `"${item.title}" was already answered on another surface`,
        "success",
      );
      load();
    } catch (e) {
      onToast(`Error: ${e}`, "error");
    } finally {
      setBusy(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }
  if (!configured) return <NotConfiguredCard />;

  return (
    <div data-component="ApprovalsSection" className="flex flex-col gap-3">
      {items.length === 0 && <EmptyCard label="No pending approvals or asks" />}
      {items.map((item) => (
        <Card key={item.id}>
          <CardContent className="flex items-start gap-4 py-4">
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-center gap-2">
                <Badge tone="outline">
                  {item.kind === "approval" ? "approval" : "ask"}
                </Badge>
                {!item.reversible && <Badge tone="outline">irreversible</Badge>}
                <span className="truncate text-sm font-medium">
                  {item.title}
                </span>
              </div>
              {item.body && (
                <div className="truncate text-xs text-muted-foreground">
                  {item.body}
                </div>
              )}
              {item.command && (
                <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
                  {item.command}
                </div>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {item.kind === "approval" ? (
                <>
                  <Button
                    size="sm"
                    className="uppercase"
                    disabled={busy === item.id}
                    onClick={() => answer(item, "approved")}
                    prefix={
                      busy === item.id ? (
                        <Spinner />
                      ) : (
                        <Check className="h-4 w-4" />
                      )
                    }
                  >
                    Approve
                  </Button>
                  <Button
                    ghost
                    size="icon"
                    title="Deny"
                    aria-label="Deny"
                    className="text-destructive"
                    disabled={busy === item.id}
                    onClick={() => answer(item, "denied")}
                  >
                    <X />
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  className="uppercase"
                  disabled={busy === item.id}
                  onClick={() => answer(item, "acknowledged")}
                  prefix={
                    busy === item.id ? <Spinner /> : <Check className="h-4 w-4" />
                  }
                >
                  Acknowledge
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function GoalsSection({
  onToast,
}: {
  onToast: (msg: string, kind: "success" | "error") => void;
}) {
  const [goals, setGoals] = useState<CommsGoal[]>([]);
  const [configured, setConfigured] = useState(true);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .getCommsGoals()
      .then((res) => {
        setConfigured(res.configured);
        setGoals(res.goals);
      })
      .catch(() => onToast("Failed to load goals", "error"))
      .finally(() => setLoading(false));
  }, [onToast]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }
  if (!configured) return <NotConfiguredCard />;

  return (
    <div data-component="GoalsSection" className="flex flex-col gap-3">
      {goals.length === 0 && <EmptyCard label="No goals visible to you" />}
      {goals.map((g) => (
        <Card key={g.id}>
          <CardContent className="flex items-start gap-4 py-4">
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-center gap-2">
                <Badge tone="outline">{g.priority}</Badge>
                <Badge tone="outline">{g.status}</Badge>
                <span className="truncate text-sm font-medium">{g.title}</span>
              </div>
              {g.description && (
                <div className="truncate text-xs text-muted-foreground">
                  {g.description}
                </div>
              )}
            </div>
            <Badge tone="outline">
              {g.visibility === "shared" ? "shared" : "private"}
            </Badge>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function ChangesSection({
  onToast,
}: {
  onToast: (msg: string, kind: "success" | "error") => void;
}) {
  const [changes, setChanges] = useState<CommsChange[]>([]);
  const [configured, setConfigured] = useState(true);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .getCommsChanges()
      .then((res) => {
        setConfigured(res.configured);
        setChanges(res.changes);
      })
      .catch(() => onToast("Failed to load changes", "error"))
      .finally(() => setLoading(false));
  }, [onToast]);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (change: CommsChange, kind: "undo" | "redo") => {
    setBusy(change.id);
    try {
      if (kind === "undo") await api.undoCommsChange(change.id);
      else await api.redoCommsChange(change.id);
      onToast(`${kind === "undo" ? "Undid" : "Redid"} ${change.target_kind} change`, "success");
      load();
    } catch (e) {
      onToast(`Error: ${e}`, "error");
    } finally {
      setBusy(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }
  if (!configured) return <NotConfiguredCard />;

  return (
    <div data-component="ChangesSection" className="flex flex-col gap-3">
      {changes.length === 0 && <EmptyCard label="No changes visible to you" />}
      {changes.map((c) => (
        <Card key={c.id}>
          <CardContent className="flex items-start gap-4 py-4">
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-center gap-2">
                <Badge tone="outline">{c.target_kind}</Badge>
                {!c.reversible && <Badge tone="outline">irreversible</Badge>}
                {c.undone && <Badge tone="outline">undone</Badge>}
                <span className="truncate font-mono text-xs">{c.id}</span>
              </div>
              <div className="text-xs text-muted-foreground">
                by {c.actor_user_id ?? "unknown"}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {c.undone ? (
                <Button
                  size="sm"
                  className="uppercase"
                  disabled={busy === c.id}
                  onClick={() => act(c, "redo")}
                  prefix={
                    busy === c.id ? <Spinner /> : <RotateCw className="h-4 w-4" />
                  }
                >
                  Redo
                </Button>
              ) : (
                <Button
                  size="sm"
                  className="uppercase"
                  disabled={busy === c.id || !c.reversible}
                  title={c.reversible ? undefined : "Irreversible — cannot undo"}
                  onClick={() => act(c, "undo")}
                  prefix={
                    busy === c.id ? (
                      <Spinner />
                    ) : (
                      <RotateCcw className="h-4 w-4" />
                    )
                  }
                >
                  Undo
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function MemorySection({
  onToast,
}: {
  onToast: (msg: string, kind: "success" | "error") => void;
}) {
  const [memories, setMemories] = useState<CommsMemory[]>([]);
  const [configured, setConfigured] = useState(true);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .getCommsMemory()
      .then((res) => {
        setConfigured(res.configured);
        setMemories(res.memories);
      })
      .catch(() => onToast("Failed to load memory", "error"))
      .finally(() => setLoading(false));
  }, [onToast]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }
  if (!configured) return <NotConfiguredCard />;

  return (
    <div data-component="MemorySection" className="flex flex-col gap-3">
      {memories.length === 0 && <EmptyCard label="No memory visible to you" />}
      {memories.map((m) => (
        <Card key={m.id}>
          <CardContent className="flex items-start gap-4 py-4">
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-center gap-2">
                <Badge tone="outline">{m.kind}</Badge>
                {m.topic && <Badge tone="outline">{m.topic}</Badge>}
              </div>
              <div className="text-sm">{m.content}</div>
            </div>
            <Badge tone="outline">
              {m.visibility === "shared" ? "shared" : "private"}
            </Badge>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

const SECTIONS: { value: Section; label: string; icon: typeof Bell }[] = [
  { value: "approvals", label: "Approvals", icon: Bell },
  { value: "goals", label: "Goals", icon: Target },
  { value: "changes", label: "Changes", icon: History },
  { value: "memory", label: "Memory", icon: Brain },
];

export default function CommsPage() {
  const [section, setSection] = useState<Section>("approvals");
  const [principal, setPrincipal] = useState<CommsPrincipal | null>(null);
  const { toast, showToast } = useToast();

  useEffect(() => {
    api
      .getCommsWhoami()
      .then((res) => setPrincipal(res.principal))
      .catch(() => setPrincipal(null));
  }, []);

  return (
    <div data-component="CommsPage" className="flex flex-col gap-6">
      <Toast toast={toast} />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Bell className="h-4 w-4" />
          Human comms
        </H2>
        <PrincipalBanner principal={principal} />
      </div>

      <Segmented
        className="w-fit"
        size="md"
        value={section}
        onChange={(v) => setSection(v as Section)}
        options={SECTIONS.map((s) => ({ value: s.value, label: s.label }))}
      />

      {section === "approvals" && <ApprovalsSection onToast={showToast} />}
      {section === "goals" && <GoalsSection onToast={showToast} />}
      {section === "changes" && <ChangesSection onToast={showToast} />}
      {section === "memory" && <MemorySection onToast={showToast} />}
    </div>
  );
}
