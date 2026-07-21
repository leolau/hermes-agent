import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CoreAreaView } from "@/components/core/CoreAreaView";
import type { Change, CoreManifestResponse } from "@/types";

const MANIFEST: CoreManifestResponse = {
  core_root: "hermes-agent",
  manifest_path: "core_manifest.yaml",
  manifest_present: true,
  manifest_parseable: true,
  fallback_active: false,
  self_protected: true,
  globs: ["agent/**", "hermes_cli/**"],
  glob_count: 2,
  audit_log_path: "/tmp/core_audit.log",
  denials: [
    {
      id: "d1",
      ts: 1,
      actor_user_id: "leo_owner",
      mode: "prod",
      summary: "refused write to agent/core_boundary.py",
    },
  ],
};

describe("CoreAreaView", () => {
  it("renders boundary health, globs, a denial, and a change", () => {
    const changes: Change[] = [
      {
        id: "chg_0123456789abcdef",
        actor_user_id: "leo_owner",
        mode: "prod",
        target_kind: "goal",
        reversible: true,
        undone: false,
        visibility: "shared",
      },
    ];
    const html = renderToStaticMarkup(
      <CoreAreaView manifest={MANIFEST} changes={changes} changesConfigured />,
    );
    expect(html).toContain('data-component="CoreAreaView"');
    expect(html).toContain("manifest active");
    expect(html).toContain("self-protected");
    expect(html).toContain("agent/**");
    expect(html).toContain("refused write to agent/core_boundary.py");
    expect(html).toContain("goal");
    expect(html).toContain("reversible");
    expect(html).toContain("Read-only");
  });

  it("shows the fail-closed fallback health when the manifest is absent", () => {
    const html = renderToStaticMarkup(
      <CoreAreaView
        manifest={{ ...MANIFEST, fallback_active: true }}
        changes={[]}
        changesConfigured
      />,
    );
    expect(html).toContain("fail-closed fallback");
    expect(html).toContain("No changes visible in your scope");
  });

  it("shows the unconfigured change-log state", () => {
    const html = renderToStaticMarkup(
      <CoreAreaView
        manifest={{ ...MANIFEST, denials: [] }}
        changes={[]}
        changesConfigured={false}
      />,
    );
    expect(html).toContain("Change log not configured");
    expect(html).toContain("No Core-write denials recorded");
  });
});
