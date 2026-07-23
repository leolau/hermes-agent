import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MembersView } from "@/components/members/MembersView";
import type { Member } from "@/types";

const OWNER: Member = {
  user_id: "leo_owner",
  display: "Leo",
  role: "owner",
  email: "",
  active: true,
  channels: ["telegram:1"],
  is_owner: true,
};

const MEMBER: Member = {
  user_id: "a1b2c3",
  display: "Mia",
  role: "member",
  email: "mia@x.io",
  active: true,
  channels: [],
  is_owner: false,
};

const DEACTIVATED: Member = {
  ...MEMBER,
  user_id: "d4e5f6",
  display: "Sam",
  email: "sam@x.io",
  active: false,
};

describe("MembersView", () => {
  it("renders the owner/admin pills and an add-member form", () => {
    const html = renderToStaticMarkup(
      <MembersView initialConfigured initialMembers={[OWNER, MEMBER]} />,
    );
    expect(html).toContain("owner/admin only");
    expect(html).toContain('data-component="AddMemberForm"');
    expect(html).toContain("Create member");
  });

  it("shows the owner row as read-only (no role/deactivate controls)", () => {
    const html = renderToStaticMarkup(
      <MembersView initialConfigured initialMembers={[OWNER]} />,
    );
    expect(html).toContain("hermes owner transfer");
    // The owner row must not offer a Reset/Deactivate action.
    expect(html).not.toContain("Deactivate");
    expect(html).not.toContain("Reset password");
  });

  it("shows management controls for a non-owner member", () => {
    const html = renderToStaticMarkup(
      <MembersView initialConfigured initialMembers={[MEMBER]} />,
    );
    expect(html).toContain("mia@x.io");
    expect(html).toContain("Reset password");
    expect(html).toContain("Deactivate");
  });

  it("marks a deactivated member and offers reactivate", () => {
    const html = renderToStaticMarkup(
      <MembersView initialConfigured initialMembers={[DEACTIVATED]} />,
    );
    expect(html).toContain("deactivated");
    expect(html).toContain("Reactivate");
  });

  it("surfaces the not-configured notice and empty roster", () => {
    const html = renderToStaticMarkup(
      <MembersView initialConfigured={false} initialMembers={[]} />,
    );
    expect(html).toContain("configured on this server yet");
    expect(html).toContain("No members enrolled yet.");
  });
});
