import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GtsCentreView } from "@/components/gts/GtsCentreView";
import type {
  GtsEvaluationMethod,
  GtsGoal,
  GtsGraphResponse,
  GtsItemGrant,
  GtsTask,
} from "@/types";

const NO_METHOD: GtsEvaluationMethod = {
  set_by_user_id: null,
  locked: false,
  measurable: false,
  observation: null,
  scoring_prompt: "",
};

function grant(over: Partial<GtsItemGrant>): GtsItemGrant {
  return {
    id: "gr",
    item_kind: "task",
    item_id: "t1",
    user_id: "alice",
    grant: "assignee",
    granted_by: "owner",
    status: "accepted",
    ...over,
  };
}

function goal(over: Partial<GtsGoal>): GtsGoal {
  return {
    id: "g1",
    owner_user_id: "owner",
    visibility: "shared",
    title: "Ship agent-home",
    priority: "high",
    status: "active",
    level: "top",
    parent_goal_id: null,
    score: 72,
    assignee_user_id: null,
    evaluation_method: NO_METHOD,
    grants: [],
    ...over,
  };
}

function task(over: Partial<GtsTask>): GtsTask {
  return {
    id: "t1",
    owner_user_id: "owner",
    visibility: "shared",
    title: "Wire the seam",
    priority: "medium",
    status: "active",
    current_state: "in_progress",
    parent_task_id: null,
    score: 40,
    assignee_user_id: null,
    evaluation_method: NO_METHOD,
    grants: [],
    ...over,
  };
}

describe("GtsCentreView", () => {
  it("renders the unconfigured state when the datastore is unset", () => {
    const graph: GtsGraphResponse = {
      configured: false,
      goals: [],
      tasks: [],
      skills: [],
      task_goals: [],
      task_skills: [],
      assignment: { enabled: true, scheme: "per-user" },
    };
    const html = renderToStaticMarkup(<GtsCentreView graph={graph} />);
    expect(html).toContain('data-component="GtsCentreView"');
    expect(html).toContain("needs the application datastore");
  });

  it("renders the empty-scope state when configured with no goals", () => {
    const graph: GtsGraphResponse = {
      configured: true,
      goals: [],
      tasks: [],
      skills: [],
      task_goals: [],
      task_skills: [],
      assignment: { enabled: true, scheme: "per-user" },
    };
    const html = renderToStaticMarkup(<GtsCentreView graph={graph} />);
    expect(html).toContain("No goals visible in your scope yet");
  });

  it("renders goals, linked tasks + skills, scores, and assignment", () => {
    const graph: GtsGraphResponse = {
      configured: true,
      principal: "owner",
      mode: "prod",
      goals: [
        goal({
          id: "g1",
          title: "Ship agent-home",
          score: 72,
          assignee_user_id: "alice",
          grants: [
            grant({ id: "a", user_id: "alice", grant: "assignee" }),
            grant({ id: "w1", user_id: "bob", grant: "watcher" }),
            grant({ id: "w2", user_id: "carol", grant: "watcher" }),
          ],
        }),
        goal({
          id: "g1a",
          title: "Sub goal",
          level: "sub",
          parent_goal_id: "g1",
          score: null,
        }),
      ],
      tasks: [
        task({
          id: "t1",
          title: "Wire the seam",
          score: 40,
          evaluation_method: {
            set_by_user_id: "owner",
            locked: true,
            measurable: true,
            observation: { source: "manual", prompt: "did the seam ship?" },
            scoring_prompt: "0 unless deployed",
          },
        }),
      ],
      skills: [
        {
          id: "s1",
          owner_user_id: "owner",
          visibility: "shared",
          name: "typescript",
          skill_ref: "lang.ts",
        },
      ],
      task_goals: [{ task_id: "t1", goal_id: "g1" }],
      task_skills: [{ task_id: "t1", skill_id: "s1" }],
      assignment: { enabled: true, scheme: "per-user" },
    };

    const html = renderToStaticMarkup(<GtsCentreView graph={graph} />);
    // Goal + nested sub-goal + linked task + skill all render.
    expect(html).toContain("Ship agent-home");
    expect(html).toContain("Sub goal");
    expect(html).toContain("Wire the seam");
    expect(html).toContain("typescript");
    // Engine-computed score is rounded and shown; assignee + watcher count.
    expect(html).toContain("72");
    expect(html).toContain("@alice");
    expect(html).toContain("2 watching");
    // Evaluation method surfaces observe/measure, never a user-set score.
    expect(html).toContain("measurable");
    expect(html).toContain("did the seam ship?");
    // Read-only marker.
    expect(html).toContain("Read-only");
  });
});
