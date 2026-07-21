import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MobileShell } from "@/components/MobileShell";

// Basic render test for the mobile shell (FG-20 Wave A). `showNav={false}`
// avoids the client-only BottomNav (usePathname) so this stays a pure
// server-render assertion.
describe("MobileShell", () => {
  it("renders the title, the data-component root, and safe-area padding", () => {
    const html = renderToStaticMarkup(
      <MobileShell title="Sign in" showNav={false}>
        <p>hello</p>
      </MobileShell>,
    );
    expect(html).toContain('data-component="MobileShell"');
    expect(html).toContain("Sign in");
    expect(html).toContain("hello");
    // safe-area inset is wired for the notch/home-indicator.
    expect(html).toContain("safe-top");
  });
});
