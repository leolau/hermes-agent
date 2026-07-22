import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApprovalsList } from "@/components/inbox/ApprovalsList";
import { ChangesList } from "@/components/inbox/ChangesList";
import { InboxView } from "@/components/inbox/InboxView";
import type { Change, Notification } from "@/types";

const APPROVAL: Notification = {
  id: "ntf_1",
  kind: "approval",
  owner_user_id: "leo_owner",
  visibility: "private:leo_owner",
  title: "Run rm -rf build/",
  body: "The agent wants to clear the build dir.",
  command: "rm -rf build/",
  reversible: false,
  status: "pending",
  answer: null,
  answered_by: null,
  answered_via: null,
  delivered: true,
  created_at: null,
  answered_at: null,
};

const ANSWERED_ASK: Notification = {
  ...APPROVAL,
  id: "ntf_2",
  kind: "ask",
  title: "Ready to deploy?",
  reversible: true,
  status: "answered",
  answer: "acknowledged",
  answered_via: "telegram",
};

const REVERSIBLE: Change = {
  id: "chg_1",
  actor_user_id: "leo_owner",
  mode: "prod",
  target_kind: "memory",
  reversible: true,
  visibility: "private:leo_owner",
  undone: false,
};

const UNDONE: Change = { ...REVERSIBLE, id: "chg_2", undone: true };
const IRREVERSIBLE: Change = {
  ...REVERSIBLE,
  id: "chg_3",
  reversible: false,
  target_kind: "tool",
};

describe("InboxView", () => {
  it("renders the C2 pills and the Approvals tab by default", () => {
    const html = renderToStaticMarkup(
      <InboxView
        initialConfigured
        initialNotifications={[APPROVAL]}
        initialChanges={[REVERSIBLE]}
      />,
    );
    expect(html).toContain('data-component="InboxView"');
    expect(html).toContain('data-component="ApprovalsList"');
    expect(html).toContain("principal-scoped (C2)");
    expect(html).toContain("Approvals");
    expect(html).toContain("Changes");
    expect(html).toContain("Run rm -rf build/");
  });

  it("shows the unconfigured datastore state", () => {
    const html = renderToStaticMarkup(
      <InboxView
        initialConfigured={false}
        initialNotifications={[]}
        initialChanges={[]}
      />,
    );
    expect(html).toContain("multi-user datastore configured");
  });
});

describe("ApprovalsList", () => {
  it("offers Approve/Deny on a pending approval and marks it irreversible", () => {
    const html = renderToStaticMarkup(
      <ApprovalsList notifications={[APPROVAL]} busyId={null} onAnswer={() => {}} />,
    );
    expect(html).toContain('data-component="ApprovalsList"');
    expect(html).toContain("Approve");
    expect(html).toContain("Deny");
    expect(html).toContain("irreversible");
    expect(html).toContain("rm -rf build/");
  });

  it("shows a settled ask with its cross-surface answer and no buttons", () => {
    const html = renderToStaticMarkup(
      <ApprovalsList
        notifications={[ANSWERED_ASK]}
        busyId={null}
        onAnswer={() => {}}
      />,
    );
    expect(html).toContain("acknowledged");
    expect(html).toContain("via telegram");
    expect(html).not.toContain(">Acknowledge<");
  });

  it("renders an empty state", () => {
    const html = renderToStaticMarkup(
      <ApprovalsList notifications={[]} busyId={null} onAnswer={() => {}} />,
    );
    expect(html).toContain("No pending approvals or asks");
  });
});

describe("ChangesList", () => {
  it("offers Undo on a live reversible change and Redo on an undone one", () => {
    const html = renderToStaticMarkup(
      <ChangesList
        changes={[REVERSIBLE, UNDONE]}
        busyId={null}
        onOp={() => {}}
      />,
    );
    expect(html).toContain('data-component="ChangesList"');
    expect(html).toContain("Undo");
    expect(html).toContain("Redo");
    expect(html).toContain("undone");
  });

  it("shows an irreversible change as review-only with no action", () => {
    const html = renderToStaticMarkup(
      <ChangesList changes={[IRREVERSIBLE]} busyId={null} onOp={() => {}} />,
    );
    expect(html).toContain("Not reversible");
    expect(html).not.toContain(">Undo<");
    expect(html).not.toContain(">Redo<");
  });
});
