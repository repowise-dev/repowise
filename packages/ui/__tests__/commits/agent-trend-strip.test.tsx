import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import type { AgentTrend } from "@repowise-dev/types/git";

import { AgentTrendStrip } from "../../src/commits/agent-trend-strip";

const trend = (over: Partial<AgentTrend> = {}): AgentTrend => ({
  enabled: true,
  total_commits: 40,
  agent_commits: 10,
  human_attested_commits: 20,
  unknown_commits: 10,
  coverage_pct: 75,
  agent_pct_of_covered: 33.3,
  suppressed_reason: null,
  coverage_floor_pct: 20,
  buckets: [],
  agent_names: [{ name: "claude", count: 10 }],
  ...over,
});

describe("AgentTrendStrip", () => {
  it("renders nothing when attribution is off", () => {
    const { container } = render(<AgentTrendStrip trend={trend({ enabled: false })} />);
    expect(container.textContent).toBe("");
  });

  it("reads a trend with no enabled flag as on when it carries the share", () => {
    const { enabled: _e, ...unflagged } = trend();
    const { container } = render(<AgentTrendStrip trend={unflagged} />);
    expect(container.textContent).toContain("33%");
  });

  it("stays hidden for a trend with neither the flag nor the share", () => {
    const { enabled: _e, agent_pct_of_covered: _p, ...older } = trend();
    const { container } = render(<AgentTrendStrip trend={older as AgentTrend} />);
    expect(container.textContent).toBe("");
  });
});
