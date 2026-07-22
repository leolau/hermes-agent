import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";

describe("MessageBubble", () => {
  it("renders a user turn aligned right with its text", () => {
    const html = renderToStaticMarkup(
      <MessageBubble message={{ role: "user", content: "hello agent" }} />,
    );
    expect(html).toContain('data-component="MessageBubble"');
    expect(html).toContain("justify-end");
    expect(html).toContain("hello agent");
  });

  it("renders an assistant turn aligned left", () => {
    const html = renderToStaticMarkup(
      <MessageBubble message={{ role: "assistant", content: "how can I help?" }} />,
    );
    expect(html).toContain("justify-start");
    expect(html).toContain("how can I help?");
  });

  it("renders inline image attachments as <img> media", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        message={{
          role: "user",
          content: "look at this ![shot](https://cdn.test/a.png)",
        }}
      />,
    );
    expect(html).toContain("look at this");
    expect(html).toContain('src="https://cdn.test/a.png"');
    expect(html).toContain('alt="shot"');
  });
});
