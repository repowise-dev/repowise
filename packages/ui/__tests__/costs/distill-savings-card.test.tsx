import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  DistillSavingsCard,
  type DistillSavingsData,
} from "../../src/costs/distill-savings-card";

function makeData(overrides: Partial<DistillSavingsData> = {}): DistillSavingsData {
  return {
    available: true,
    events: 12,
    raw_tokens: 70_000,
    distilled_tokens: 5_000,
    saved_tokens: 65_000,
    estimated_usd_saved: 1.5,
    pricing_model: "claude-opus-4-8",
    pricing_agent: "claude_code",
    pricing_source: "detected from Claude Code session",
    per_filter: [
      { group: "git_log", events: 1, raw_tokens: 31_000, distilled_tokens: 500, saved_tokens: 30_500 },
    ],
    per_day: [],
    mcp_events: 5,
    mcp_tokens: 39_000,
    mcp_per_tool: [
      { tool: "get_risk", events: 3, tokens: 29_000 },
      { tool: "get_dead_code", events: 1, tokens: 7_000 },
    ],
    missed_events: 0,
    missed_tokens_est: 0,
    missed_window_days: 7,
    ...overrides,
  };
}

describe("DistillSavingsCard", () => {
  it("shows the hero total as distill + mcp saved tokens", () => {
    render(<DistillSavingsCard data={makeData()} />);
    // 65K + 39K = 104K → formatTokens → "104K"
    expect(screen.getByText("104K")).toBeInTheDocument();
    expect(screen.getByText(/priced at Claude Code/)).toBeInTheDocument();
  });

  it("breaks savings down by surface", () => {
    render(<DistillSavingsCard data={makeData()} />);
    expect(screen.getByText("Distill — by filter")).toBeInTheDocument();
    expect(screen.getByText("MCP — by tool")).toBeInTheDocument();
    expect(screen.getByText("get_risk")).toBeInTheDocument();
  });

  it("renders the missed-as-opportunity CTA with a docs link", () => {
    render(
      <DistillSavingsCard
        data={makeData({ missed_events: 193, missed_tokens_est: 74_000 })}
      />,
    );
    expect(screen.getByText(/Unlock ~74K more/)).toBeInTheDocument();
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", expect.stringContaining("DISTILL.md"));
  });

  it("frames MCP savings as queries answered when counterfactuals exist", () => {
    render(
      <DistillSavingsCard
        data={makeData({
          mcp_queries: 8,
          mcp_per_tool: [
            { tool: "get_context", events: 6, tokens: 30_000, kind: "counterfactual" },
            { tool: "get_risk", events: 2, tokens: 9_000, kind: "truncation" },
          ],
        })}
      />,
    );
    expect(screen.getByText(/8 queries answered/)).toBeInTheDocument();
  });

  it("falls back to the drop count when no counterfactual queries exist", () => {
    render(<DistillSavingsCard data={makeData({ mcp_queries: 0 })} />);
    expect(screen.getByText(/5 drops/)).toBeInTheDocument();
  });

  it("shows the empty state when nothing is saved", () => {
    render(<DistillSavingsCard data={makeData({ available: false, saved_tokens: 0, mcp_tokens: 0 })} />);
    expect(screen.getByText(/No agent token savings recorded yet/)).toBeInTheDocument();
  });

  it("renders MCP-only savings even with no distill events", () => {
    render(
      <DistillSavingsCard
        data={makeData({ events: 0, saved_tokens: 0, per_filter: [] })}
      />,
    );
    // 0 + 39K = 39K total (hero) — also appears as the MCP sub-stat, so match
    // at least one occurrence rather than asserting uniqueness.
    expect(screen.getAllByText("39K").length).toBeGreaterThanOrEqual(1);
  });
});
