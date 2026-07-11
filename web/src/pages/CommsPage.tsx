import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  Bell,
  Check,
  Target,
  History,
  Brain,
  Link2,
  Plus,
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
import { Input } from "@nous-research/ui/ui/components/input";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { api } from "@/lib/api";
import type {
  CommsChange,
  CommsGoal,
  CommsGoalContext,
  CommsGoalResourceKind,
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

function GoalCard({
  goal,
  onChanged,
  onToast,
}: {
  goal: CommsGoal;
  onChanged: () => void;
  onToast: (msg: string, kind: "success" | "error") => void;
}) {
  const [busy, setBusy] = useState(false);
  const [resourceKind, setResourceKind] =
    useState<CommsGoalResourceKind>("task");
  const [resourceRef, setResourceRef] = useState("");
  const [taskId, setTaskId] = useState("");
  const [taskState, setTaskState] = useState("");
  const [metricName, setMetricName] = useState("");
  const [metricValue, setMetricValue] = useState("");
  const [note, setNote] = useState("");
  const [context, setContext] = useState<CommsGoalContext | null>(null);

  const act = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true);
    try {
      await operation();
      onToast(success, "success");
      onChanged();
      return true;
    } catch (error) {
      onToast(`Error: ${error}`, "error");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const link = async (event: FormEvent) => {
    event.preventDefault();
    if (!resourceRef.trim()) return;
    const changed = await act(
      () => api.linkCommsGoal(goal.id, resourceKind, resourceRef.trim()),
      `Linked ${resourceKind}`,
    );
    if (changed) setResourceRef("");
  };

  const advanceTask = (event: FormEvent) => {
    event.preventDefault();
    if (!taskId.trim() || !taskState.trim()) return;
    void act(
      () =>
        api.advanceCommsGoal(goal.id, {
          target: "task",
          task_id: taskId.trim(),
          state: taskState.trim(),
        }),
      "Task advanced",
    );
  };

  const advanceMetric = async (event: FormEvent) => {
    event.preventDefault();
    const value = Number(metricValue);
    if (!metricName.trim() || !metricValue.trim() || !Number.isFinite(value)) {
      return;
    }
    const changed = await act(
      () =>
        api.advanceCommsGoal(goal.id, {
          target: "metric",
          metric_name: metricName.trim(),
          value,
        }),
      "Metric advanced",
    );
    if (changed) setMetricValue("");
  };

  const addProgress = async (event: FormEvent) => {
    event.preventDefault();
    if (!note.trim()) return;
    const changed = await act(
      () =>
        api.advanceCommsGoal(goal.id, {
          target: "note",
          note: note.trim(),
      }),
      "Progress recorded",
    );
    if (changed) setNote("");
  };

  const loadContext = async () => {
    setBusy(true);
    try {
      const response = await api.getCommsGoalContext(goal.id);
      setContext(response.context);
    } catch (error) {
      onToast(`Error: ${error}`, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card data-component="GoalCard">
      <CardContent className="flex flex-col gap-4 py-4">
        <div className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="mb-1 flex items-center gap-2">
              <Badge tone="outline">{goal.priority}</Badge>
              <Badge tone="outline">{goal.status}</Badge>
              <span className="truncate text-sm font-medium">{goal.title}</span>
            </div>
            {goal.description && (
              <div className="truncate text-xs text-muted-foreground">
                {goal.description}
              </div>
            )}
            <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
              {goal.id}
            </div>
          </div>
          <Badge tone="outline">
            {goal.visibility === "shared" ? "shared" : "private"}
          </Badge>
        </div>

        <div className="grid gap-2 md:grid-cols-2">
          <label className="flex items-center gap-2 text-xs">
            Priority
            <select
              className="h-9 flex-1 border border-border bg-background px-2"
              value={goal.priority}
              disabled={busy}
              onChange={(event) =>
                void act(
                  () => api.prioritiseCommsGoal(goal.id, event.target.value),
                  "Priority updated",
                )
              }
            >
              <option value="critical">critical</option>
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </select>
          </label>
          <div className="flex justify-end gap-2">
            <Button
              ghost
              size="sm"
              disabled={busy}
              onClick={() => void loadContext()}
            >
              Context
            </Button>
            <Button
              size="sm"
              disabled={busy || goal.status === "done"}
              onClick={() =>
                void act(() => api.closeCommsGoal(goal.id), "Goal closed")
              }
            >
              Close
            </Button>
          </div>
        </div>

        <form className="flex gap-2" onSubmit={link}>
          <select
            className="h-9 border border-border bg-background px-2 text-xs"
            value={resourceKind}
            onChange={(event) =>
              setResourceKind(event.target.value as CommsGoalResourceKind)
            }
          >
            <option value="memory">memory</option>
            <option value="task">task</option>
            <option value="tool">tool</option>
          </select>
          <Input
            aria-label="Resource reference"
            value={resourceRef}
            onChange={(event) => setResourceRef(event.target.value)}
            placeholder="Resource ID or tool name"
          />
          <Button
            type="submit"
            size="sm"
            disabled={busy || !resourceRef.trim()}
            prefix={<Link2 className="h-4 w-4" />}
          >
            Link
          </Button>
        </form>

        <form className="grid gap-2 md:grid-cols-[1fr_1fr_auto]" onSubmit={advanceTask}>
          <Input
            aria-label="Linked task ID"
            value={taskId}
            onChange={(event) => setTaskId(event.target.value)}
            placeholder="Linked task ID"
          />
          <Input
            aria-label="Task state"
            value={taskState}
            onChange={(event) => setTaskState(event.target.value)}
            placeholder="Next task state"
          />
          <Button type="submit" size="sm" disabled={busy}>
            Advance task
          </Button>
        </form>

        <form
          className="grid gap-2 md:grid-cols-[1fr_1fr_auto]"
          onSubmit={advanceMetric}
        >
          <Input
            aria-label="Goal metric name"
            value={metricName}
            onChange={(event) => setMetricName(event.target.value)}
            placeholder="Metric name"
          />
          <Input
            aria-label="Goal metric value"
            type="number"
            value={metricValue}
            onChange={(event) => setMetricValue(event.target.value)}
            placeholder="Current value"
          />
          <Button
            type="submit"
            size="sm"
            disabled={
              busy ||
              !metricName.trim() ||
              !metricValue.trim() ||
              !Number.isFinite(Number(metricValue))
            }
          >
            Advance metric
          </Button>
        </form>

        <form className="flex gap-2" onSubmit={addProgress}>
          <Input
            aria-label="Goal progress note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Progress update"
          />
          <Button type="submit" size="sm" disabled={busy || !note.trim()}>
            Add progress
          </Button>
        </form>

        {context && (
          <div className="border-t border-border pt-3 text-xs">
            <div className="mb-2 font-medium">
              Linked context ({context.resources.length})
            </div>
            <div className="flex flex-wrap gap-2">
              {context.resources.map(({ link: linked }) => (
                <Badge
                  key={`${linked.resource_kind}:${linked.resource_ref}`}
                  tone="outline"
                >
                  {linked.resource_kind}: {linked.resource_ref}
                </Badge>
              ))}
              {context.resources.length === 0 && (
                <span className="text-muted-foreground">
                  No visible linked resources
                </span>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
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
  const [title, setTitle] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    api
      .getCommsGoals()
      .then((res) => {
        setConfigured(res.configured);
        setGoals(res.goals);
      })
      .catch(() => onToast("Failed to load goals", "error"))
      .finally(() => setLoading(false));
  }, [onToast]);

  useEffect(() => {
    load();
  }, [load]);

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim()) return;
    setCreating(true);
    try {
      await api.createCommsGoal({ title: title.trim() });
      setTitle("");
      onToast("Goal created", "success");
      load();
    } catch (error) {
      onToast(`Error: ${error}`, "error");
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return (
      <div
        data-component="GoalsSection"
        className="flex items-center justify-center py-16"
      >
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }
  if (!configured) return <NotConfiguredCard />;

  return (
    <div data-component="GoalsSection" className="flex flex-col gap-3">
      <form className="flex gap-2" onSubmit={create}>
        <Input
          aria-label="New goal title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Create a durable goal"
        />
        <Button
          type="submit"
          disabled={creating || !title.trim()}
          prefix={creating ? <Spinner /> : <Plus className="h-4 w-4" />}
        >
          Create
        </Button>
      </form>
      {goals.length === 0 && <EmptyCard label="No goals visible to you" />}
      {goals.map((goal) => (
        <GoalCard
          key={goal.id}
          goal={goal}
          onChanged={load}
          onToast={onToast}
        />
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
