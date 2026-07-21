import { describe, expect, it } from "vitest";

import { scopedMediaPath } from "@/lib/supabase/storage";
import type { Principal } from "@/types";

function principal(over: Partial<Principal> = {}): Principal {
  return {
    user_id: "leo_owner",
    display: "Leo",
    role: "owner",
    channels: [],
    is_owner: true,
    ...over,
  };
}

describe("scopedMediaPath", () => {
  it("prefixes the object key with the principal's user_id", () => {
    const path = scopedMediaPath(principal(), "home_1", "photo.png", "abc");
    expect(path).toBe("leo_owner/home_1/abc-photo.png");
  });

  it("uses a fresh-conversation prefix when there is no session yet", () => {
    const path = scopedMediaPath(principal(), "", "a.jpg", "u1");
    expect(path.startsWith("leo_owner/new/")).toBe(true);
  });

  it("neutralises path traversal in every segment", () => {
    const path = scopedMediaPath(
      principal({ user_id: "../../etc" }),
      "../../../root",
      "../../evil.sh",
      "x/y",
    );
    expect(path).not.toContain("..");
    expect(path.split("/")).toHaveLength(3);
    // The crafted user id can never escape its own prefix segment.
    expect(path.startsWith("etc/")).toBe(true);
  });
});
