import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TraceDetail } from "@/components/activity/TraceDetail";
import { TraceTimeline } from "@/components/activity/TraceTimeline";
import type { TraceDetailResponse, TraceRow, TraceSummary } from "@/types";

const TRACE: TraceSummary = {
  trace_id: "trc_abc123",
  first_ts: "2026-07-21T10:00:00Z",
  last_ts: "2026-07-21T10:05:00Z",
  actor_user_id: "leo_owner",
  session_key: "sess-1",
  platform: "telegram",
  mode: "prod",
  event_count: 4,
  kind_counts: { inbound: 1, turn: 1, outbound: 2 },
  rolled_up: false,
};

describe("TraceTimeline", () => {
  it("lists a trace with platform, event count, kind breakdown, and a detail link", () => {
    const html = renderToStaticMarkup(
      <TraceTimeline traces={[TRACE]} configured />,
    );
    expect(html).toContain('data-component="TraceTimeline"');
    expect(html).toContain("telegram");
    expect(html).toContain("4 events");
    expect(html).toContain("inbound · 1");
    expect(html).toContain("outbound · 2");
    expect(html).toContain("/activity/trc_abc123");
  });

  it("shows the unconfigured state", () => {
    const html = renderToStaticMarkup(
      <TraceTimeline traces={[]} configured={false} />,
    );
    expect(html).toContain("Trace ledger not configured");
  });

  it("shows the empty-scope state", () => {
    const html = renderToStaticMarkup(<TraceTimeline traces={[]} configured />);
    expect(html).toContain("No traces visible in your scope yet");
  });
});

describe("TraceDetail", () => {
  it("renders the trace id, rollup, and ordered events", () => {
    const events: TraceRow[] = [
      {
        id: "int_1",
        trace_id: "trc_abc123",
        parent_id: null,
        ts: "2026-07-21T10:00:00Z",
        actor_user_id: "leo_owner",
        session_key: "sess-1",
        platform: "telegram",
        kind: "inbound",
        ref: "msg-1",
        summary: "hello there",
        payload_ref: null,
        mode: "prod",
      },
      {
        id: "int_2",
        trace_id: "trc_abc123",
        parent_id: "int_1",
        ts: "2026-07-21T10:00:05Z",
        actor_user_id: "leo_owner",
        session_key: "sess-1",
        platform: "telegram",
        kind: "tool_call",
        ref: "call-1",
        summary: "terminal",
        payload_ref: null,
        mode: "prod",
      },
    ];
    const detail: TraceDetailResponse = {
      configured: true,
      principal: "owner",
      trace_id: "trc_abc123",
      interactions: events,
      rollup: TRACE,
    };
    const html = renderToStaticMarkup(<TraceDetail detail={detail} />);
    expect(html).toContain('data-component="TraceDetail"');
    expect(html).toContain("trc_abc123");
    expect(html).toContain("inbound");
    expect(html).toContain("hello there");
    expect(html).toContain("tool_call");
    expect(html).toContain("4 events");
    expect(html).toContain("All traces");
  });

  it("shows the unconfigured state", () => {
    const detail: TraceDetailResponse = {
      configured: false,
      trace_id: "trc_x",
      interactions: [],
      rollup: null,
    };
    const html = renderToStaticMarkup(<TraceDetail detail={detail} />);
    expect(html).toContain("Trace ledger not configured");
  });
});
