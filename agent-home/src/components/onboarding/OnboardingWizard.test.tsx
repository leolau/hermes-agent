import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { OnboardingWizard } from "@/components/onboarding/OnboardingWizard";
import { ToolsRegistry } from "@/components/tools/ToolsRegistry";
import type {
  OnboardingItem,
  OnboardingReadinessResponse,
  Tool,
  ToolsResponse,
} from "@/types";

function item(over: Partial<OnboardingItem>): OnboardingItem {
  return {
    key: "k",
    label: "Item",
    required: true,
    rationale: "why",
    fix_command: "",
    contract: "C",
    met: true,
    detail: "",
    ...over,
  };
}

const READINESS: OnboardingReadinessResponse = {
  score: 0.75,
  score_pct: 75,
  ready_for_prod: false,
  required_total: 2,
  required_met: 1,
  optional_total: 1,
  optional_met: 1,
  optional_coverage: 1,
  missing_required: ["supabase_dsn"],
  items: [
    item({ key: "model", label: "Model configured", met: true }),
    item({
      key: "supabase_dsn",
      label: "Supabase DSN",
      met: false,
      fix_command: "hermes setup essentials",
      rationale: "Needed for the datastore.",
    }),
    item({ key: "telegram", label: "Telegram token", required: false, met: true }),
  ],
};

describe("OnboardingWizard", () => {
  it("renders the score, prod gate, and required item with its fix command", () => {
    const html = renderToStaticMarkup(<OnboardingWizard readiness={READINESS} />);
    expect(html).toContain('data-component="OnboardingWizard"');
    expect(html).toContain("75%");
    expect(html).toContain("setup incomplete");
    expect(html).toContain("Model configured");
    expect(html).toContain("Supabase DSN");
    expect(html).toContain("hermes setup essentials");
    expect(html).toContain("Required: 1/2");
  });

  it("shows the ready-for-prod gate when complete", () => {
    const html = renderToStaticMarkup(
      <OnboardingWizard
        readiness={{ ...READINESS, ready_for_prod: true, score_pct: 100 }}
      />,
    );
    expect(html).toContain("ready for prod");
  });
});

function tool(over: Partial<Tool>): Tool {
  return {
    id: "t1",
    name: "terminal",
    kind: "builtin",
    stack: "core",
    owner_user_id: "leo_owner",
    visibility: "shared",
    mode: "prod",
    status: "enabled",
    enabled: true,
    mcp_endpoint_ref: null,
    web_url: null,
    config_json: {},
    ...over,
  };
}

describe("ToolsRegistry", () => {
  it("splits tools into enabled/disabled with mode + count", () => {
    const data: ToolsResponse = {
      configured: true,
      mode: "prod",
      tools: [
        tool({ id: "t1", name: "terminal", enabled: true }),
        tool({ id: "t2", name: "spotify", enabled: false, status: "disabled" }),
      ],
    };
    const html = renderToStaticMarkup(<ToolsRegistry data={data} />);
    expect(html).toContain('data-component="ToolsRegistry"');
    expect(html).toContain("2 tools");
    expect(html).toContain("terminal");
    expect(html).toContain("Enabled (1)");
    expect(html).toContain("Disabled (1)");
    expect(html).toContain("spotify");
  });

  it("shows the unconfigured state", () => {
    const html = renderToStaticMarkup(
      <ToolsRegistry data={{ configured: false, mode: "prod", tools: [] }} />,
    );
    expect(html).toContain("Tool registry not configured");
  });
});
