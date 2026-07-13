import { useCallback, useEffect, useMemo, useState } from "react";
import { Eye, Gauge, ListTree, Target, UserCheck, Wrench } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type {
  GtsEvaluationMethod,
  GtsGoal,
  GtsGraphResponse,
  GtsItemGrant,
  GtsTask,
} from "@/lib/api";

// FG-19: show the active assignee + read-only watcher count on a node. A grant
// only confers access while pending/accepted, so declined/revoked are hidden.
function AssignmentBadges({
  assigneeUserId,
  grants,
}: {
  assigneeUserId: string | null;
  grants: GtsItemGrant[];
}) {
  const active = grants.filter(
    (g) => g.status === "accepted" || g.status === "pending",
  );
  const watchers = active.filter((g) => g.grant === "watcher");
  if (!assigneeUserId && watchers.length === 0) return null;
  return (
    <>
      {assigneeUserId ? (
        <Badge tone="secondary">
          <UserCheck className="mr-1 h-3 w-3" />
          {assigneeUserId}
        </Badge>
      ) : null}
      {watchers.length > 0 ? (
        <Badge tone="secondary">
          <Eye className="mr-1 h-3 w-3" />
          {watchers.length}
        </Badge>
      ) : null}
    </>
  );
}

const PRIORITY_TONE: Record<string, "success" | "warning" | "secondary"> = {
  high: "warning",
  medium: "secondary",
  low: "secondary",
};

function ScoreBadge({ score }: { score: number | null }) {
  if (score === null || score === undefined) {
    return <Badge tone="secondary">no score</Badge>;
  }
  const tone = score >= 66 ? "success" : score >= 33 ? "warning" : "secondary";
  return (
    <Badge tone={tone}>
      <Gauge className="mr-1 h-3 w-3" />
      {Math.round(score)}
    </Badge>
  );
}

function MethodLine({ method }: { method: GtsEvaluationMethod }) {
  if (!method.observation && !method.scoring_prompt && !method.set_by_user_id) {
    return (
      <div className="text-xs text-muted-foreground">
        No evaluation method set.
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1 text-xs text-muted-foreground">
      <div className="flex items-center gap-2">
        <Badge tone={method.measurable ? "success" : "secondary"}>
          {method.measurable ? "measurable" : "qualitative"}
        </Badge>
        {method.locked ? <Badge tone="secondary">locked</Badge> : null}
        {method.observation ? (
          <span>observe via {method.observation.source}</span>
        ) : null}
      </div>
      {method.observation?.prompt ? (
        <div>
          <span className="font-medium">Observe:</span>{" "}
          {method.observation.prompt}
        </div>
      ) : null}
      {method.scoring_prompt ? (
        <div>
          <span className="font-medium">Score:</span> {method.scoring_prompt}
        </div>
      ) : null}
    </div>
  );
}

export default function GtsCentrePage() {
  const [graph, setGraph] = useState<GtsGraphResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { setEnd } = usePageHeader();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setGraph(await api.getGtsGraph());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load GTS graph");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setEnd(<Badge tone="secondary">read-only · GTS Centre (C9)</Badge>);
    return () => setEnd(null);
  }, [setEnd]);

  // Index tasks/skills by goal via the M:N edges so each goal renders its
  // linked tasks, and each task its linked skills.
  const { tasksByGoal, skillsByTask, tasksById, skillsById } = useMemo(() => {
    const tasksById = new Map<string, GtsTask>();
    (graph?.tasks ?? []).forEach((t) => tasksById.set(t.id, t));
    const skillsById = new Map(
      (graph?.skills ?? []).map((s) => [s.id, s] as const),
    );
    const tasksByGoal = new Map<string, string[]>();
    (graph?.task_goals ?? []).forEach(({ goal_id, task_id }) => {
      const arr = tasksByGoal.get(goal_id) ?? [];
      arr.push(task_id);
      tasksByGoal.set(goal_id, arr);
    });
    const skillsByTask = new Map<string, string[]>();
    (graph?.task_skills ?? []).forEach(({ task_id, skill_id }) => {
      const arr = skillsByTask.get(task_id) ?? [];
      arr.push(skill_id);
      skillsByTask.set(task_id, arr);
    });
    return { tasksByGoal, skillsByTask, tasksById, skillsById };
  }, [graph]);

  if (loading) {
    return (
      <div
        data-component="GtsCentrePage"
        className="flex items-center gap-2 text-muted-foreground"
      >
        <Spinner /> Loading GTS Centre…
      </div>
    );
  }

  const topGoals = (graph?.goals ?? []).filter((g) => g.level === "top");
  const subGoalsByParent = new Map<string, GtsGoal[]>();
  (graph?.goals ?? [])
    .filter((g) => g.level !== "top" && g.parent_goal_id)
    .forEach((g) => {
      const arr = subGoalsByParent.get(g.parent_goal_id as string) ?? [];
      arr.push(g);
      subGoalsByParent.set(g.parent_goal_id as string, arr);
    });

  const renderGoal = (goal: GtsGoal, depth: number) => {
    const linkedTaskIds = tasksByGoal.get(goal.id) ?? [];
    const children = subGoalsByParent.get(goal.id) ?? [];
    return (
      <Card key={goal.id} style={{ marginLeft: depth * 16 }}>
        <CardContent className="p-4 flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-2 flex-wrap">
              <Target className="h-4 w-4" />
              <span className="font-medium">{goal.title}</span>
              <Badge tone={PRIORITY_TONE[goal.priority] ?? "secondary"}>
                {goal.priority}
              </Badge>
              <Badge tone="secondary">{goal.status}</Badge>
              <Badge tone="secondary">{goal.level}</Badge>
              <Badge tone="secondary">{goal.visibility}</Badge>
              <AssignmentBadges
                assigneeUserId={goal.assignee_user_id}
                grants={goal.grants}
              />
            </div>
            <ScoreBadge score={goal.score} />
          </div>
          <MethodLine method={goal.evaluation_method} />
          {linkedTaskIds.length > 0 ? (
            <div className="flex flex-col gap-2 border-l border-border/60 pl-3">
              {linkedTaskIds.map((taskId) => {
                const task = tasksById.get(taskId);
                if (!task) return null;
                const linkedSkillIds = skillsByTask.get(task.id) ?? [];
                return (
                  <div key={task.id} className="flex flex-col gap-1">
                    <div className="flex items-center gap-2 flex-wrap text-sm">
                      <ListTree className="h-4 w-4" />
                      <span>{task.title}</span>
                      <Badge tone={PRIORITY_TONE[task.priority] ?? "secondary"}>
                        {task.priority}
                      </Badge>
                      <Badge tone="secondary">{task.current_state}</Badge>
                      <AssignmentBadges
                        assigneeUserId={task.assignee_user_id}
                        grants={task.grants}
                      />
                      <ScoreBadge score={task.score} />
                    </div>
                    {linkedSkillIds.length > 0 ? (
                      <div className="flex items-center gap-1 flex-wrap pl-6 text-xs text-muted-foreground">
                        <Wrench className="h-3 w-3" />
                        {linkedSkillIds.map((sid) => (
                          <Badge key={sid} tone="secondary">
                            {skillsById.get(sid)?.name ?? sid}
                          </Badge>
                        ))}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : null}
          {children.map((child) => renderGoal(child, depth + 1))}
        </CardContent>
      </Card>
    );
  };

  return (
    <div data-component="GtsCentrePage" className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <H2>GTS Centre</H2>
      </div>

      {error ? (
        <Card>
          <CardContent className="p-5 text-sm text-destructive">
            {error}
          </CardContent>
        </Card>
      ) : null}

      {graph && !graph.configured ? (
        <Card>
          <CardContent className="p-5 text-sm text-muted-foreground">
            The GTS Centre needs the application datastore. Set{" "}
            <code>datastore.supabase_app.dsn</code> in your config.yaml, then
            create goals with <code>hermes goal</code> / the GTS tools.
          </CardContent>
        </Card>
      ) : null}

      {graph?.configured && topGoals.length === 0 ? (
        <Card>
          <CardContent className="p-5 text-sm text-muted-foreground">
            No goals visible in your scope yet.
          </CardContent>
        </Card>
      ) : null}

      {graph?.configured && topGoals.length > 0 ? (
        <div className="flex flex-col gap-3">
          {topGoals.map((goal) => renderGoal(goal, 0))}
        </div>
      ) : null}

      {graph?.configured ? (
        <div className="text-xs text-muted-foreground">
          Assignment: {graph.assignment.scheme}
          {graph.assignment.enabled ? " (FG-19: assignee + watchers)" : ""}.
        </div>
      ) : null}
    </div>
  );
}
