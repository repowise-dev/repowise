import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SavingsCard, type SavingsData } from "../../src/costs/savings-card";

function makeData(overrides: Partial<SavingsData> = {}): SavingsData {
  return {
    available: true,
    unique_events: 12,
    mcp_queries_answered: 8,
    saved_input_tokens: 104_000,
    measured_saved_input_tokens: 65_000,
    inferred_saved_input_tokens: 39_000,
    priced_saved_input_tokens: 104_000,
    unpriced_saved_input_tokens: 0,
    priced_input_savings_usd: 1.5,
    per_operation: [
      { group: "git_log", events: 1, saved_input_tokens: 30_500 },
      { group: "get_risk", events: 3, saved_input_tokens: 29_000 },
    ],
    per_surface: [
      { group: "distill", events: 7, saved_input_tokens: 65_000 },
      { group: "mcp", events: 5, saved_input_tokens: 39_000 },
    ],
    per_agent: [
      { agent: "claude_code", agent_display_name: "Claude Code", events: 12, saved_input_tokens: 104_000 },
    ],
    missed_events: 0,
    missed_tokens_est: 0,
    missed_window_days: 7,
    ...overrides,
  };
}

describe("SavingsCard", () => {
  it("shows the canonical total as the hero figure", () => {
    render(<SavingsCard data={makeData()} />);
    expect(screen.getByText("104K")).toBeInTheDocument();
  });

  it("calls the total estimated while any of it is inferred", () => {
    render(<SavingsCard data={makeData()} />);
    expect(screen.getByText(/Estimated agent savings/)).toBeInTheDocument();
  });

  it("drops the estimated framing when every token is measured", () => {
    render(
      <SavingsCard
        data={makeData({
          measured_saved_input_tokens: 104_000,
          inferred_saved_input_tokens: 0,
        })}
      />,
    );
    expect(screen.getByText(/Tokens saved for your agent/)).toBeInTheDocument();
    expect(screen.queryByText(/Estimated agent savings/)).not.toBeInTheDocument();
  });

  it("keeps the measured and inferred split legible", () => {
    render(<SavingsCard data={makeData()} />);
    // Both words also appear in the methodology note, so this asserts they are
    // present at all rather than that they appear exactly once.
    expect(screen.getAllByText(/Measured/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Inferred/).length).toBeGreaterThanOrEqual(1);
    // The legend carries the actual split, not just the labels.
    expect(screen.getByText("65K")).toBeInTheDocument();
    expect(screen.getByText("39K")).toBeInTheDocument();
  });

  it("says how much of the total the dollar figure covers when some is unpriced", () => {
    render(
      <SavingsCard
        data={makeData({
          priced_saved_input_tokens: 64_000,
          unpriced_saved_input_tokens: 40_000,
        })}
      />,
    );
    expect(screen.getByText(/priced on 64K of 104K tokens/)).toBeInTheDocument();
  });

  it("breaks savings down by surface and operation", () => {
    render(<SavingsCard data={makeData()} />);
    expect(screen.getByText("By surface")).toBeInTheDocument();
    expect(screen.getByText("By operation")).toBeInTheDocument();
    // Surface slugs are rendered as words, not as identifiers.
    expect(screen.getByText("Distill")).toBeInTheDocument();
    expect(screen.getByText("MCP")).toBeInTheDocument();
    expect(screen.getByText("get_risk")).toBeInTheDocument();
  });

  it("labels a null breakdown group rather than rendering an empty row", () => {
    render(
      <SavingsCard
        data={makeData({
          per_surface: [{ group: null, events: 1, saved_input_tokens: 104_000 }],
        })}
      />,
    );
    expect(screen.getAllByText("Unknown").length).toBeGreaterThanOrEqual(1);
  });

  it("presents a missed scan as an observed opportunity, not as a saving", () => {
    render(<SavingsCard data={makeData({ missed_events: 193, missed_tokens_est: 74_000 })} />);
    expect(screen.getByText(/Observed opportunity: ~74K/)).toBeInTheDocument();
    // The headline is untouched by what was not saved.
    expect(screen.getByText("104K")).toBeInTheDocument();
  });

  it("gives the opportunity a plain guidance link rather than a fake action", () => {
    render(<SavingsCard data={makeData({ missed_events: 193, missed_tokens_est: 74_000 })} />);
    const link = screen.getByRole("link", { name: /distillation setup guide/i });
    expect(link).toHaveAttribute("href", expect.stringContaining("DISTILL.md"));
  });

  it("does not claim a re-read would certainly have been replaced", () => {
    render(<SavingsCard data={makeData({ reread_events: 5, reread_tokens_est: 635 })} />);
    expect(screen.getByText(/Potentially avoid ~635/)).toBeInTheDocument();
    expect(screen.queryByText(/would have replaced/)).not.toBeInTheDocument();
  });

  it("shows the empty state when nothing is saved", () => {
    render(<SavingsCard data={makeData({ available: false, saved_input_tokens: 0 })} />);
    expect(screen.getByText(/No agent token savings recorded yet/)).toBeInTheDocument();
  });

  it("renders a skeleton rather than a zero while loading", () => {
    render(<SavingsCard />);
    expect(screen.queryByText("104K")).not.toBeInTheDocument();
    expect(screen.queryByText(/No agent token savings recorded yet/)).not.toBeInTheDocument();
  });
});
