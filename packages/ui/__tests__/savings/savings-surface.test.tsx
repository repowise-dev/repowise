import { describe, it, expect, beforeAll, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { SavingsLede } from "../../src/savings/savings-lede";
import { SavingsSourceTable } from "../../src/savings/savings-source-table";
import {
  OpportunityList,
  buildOpportunities,
} from "../../src/savings/opportunity-list";
import {
  SavingsTimeline,
  MIN_DAYS_FOR_CHART,
} from "../../src/savings/savings-timeline";
import { SpendSummary } from "../../src/savings/spend-summary";
import { UsageDetails } from "../../src/savings/usage-details";
import {
  SavingsMethodology,
  SavingsResetNotice,
} from "../../src/savings/savings-methodology";
import { surfaceLabel, type SavingsView, type SpendView } from "../../src/savings/types";

// jsdom has no scrollIntoView; `ViewTabs` keeps the active tab in view on mount.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

function makeSavings(overrides: Partial<SavingsView> = {}): SavingsView {
  return {
    available: true,
    window_days: null,
    as_of: "2026-09-19T00:00:00Z",
    first_event_at: "2026-09-01T00:00:00Z",
    last_event_at: "2026-09-19T00:00:00Z",
    unique_events: 120,
    successful_or_usable_partial_events: 118,
    saving_interactions: 90,
    mcp_queries_answered: 75,
    dead_ends: 2,
    saved_input_tokens: 1_000_000,
    measured_saved_input_tokens: 250_000,
    inferred_saved_input_tokens: 750_000,
    priced_saved_input_tokens: 400_000,
    unpriced_saved_input_tokens: 600_000,
    priced_input_savings_usd: 1.25,
    saved_output_tokens: null,
    priced_saved_output_tokens: 0,
    unpriced_saved_output_tokens: 0,
    priced_output_savings_usd: 0,
    per_operation: [
      { group: "get_answer", events: 40, saved_input_tokens: 600_000 },
      { group: "get_context", events: 20, saved_input_tokens: 400_000 },
    ],
    per_surface: [
      { group: "mcp", events: 50, saved_input_tokens: 750_000 },
      { group: "distill", events: 10, saved_input_tokens: 250_000 },
    ],
    per_agent: [
      {
        agent: "claude_code",
        agent_display_name: "Claude Code",
        events: 50,
        saved_input_tokens: 700_000,
      },
      {
        agent: "codex",
        agent_display_name: null,
        events: 10,
        saved_input_tokens: 300_000,
      },
    ],
    per_model: [
      { group: "claude-opus-5", events: 30, saved_input_tokens: 400_000 },
      { group: null, events: 30, saved_input_tokens: 600_000 },
    ],
    per_day: [
      { group: "2026-09-16", events: 10, saved_input_tokens: 100_000 },
      { group: "2026-09-17", events: 10, saved_input_tokens: 300_000 },
      { group: "2026-09-18", events: 10, saved_input_tokens: 200_000 },
      { group: "2026-09-19", events: 10, saved_input_tokens: 400_000 },
    ],
    opportunity_count: 0,
    opportunity_tokens_excluded: 0,
    per_opportunity_kind: [],
    missed_events: 213,
    missed_tokens_est: 96_000,
    missed_window_days: 7,
    reread_events: 5,
    reread_tokens_est: 635,
    ...overrides,
  };
}

describe("SavingsLede", () => {
  it("calls the total estimated while any of it is inferred", () => {
    render(<SavingsLede data={makeSavings()} />);
    expect(screen.getByText("Estimated agent savings")).toBeInTheDocument();
  });

  it("drops 'estimated' only when every token is measured", () => {
    render(
      <SavingsLede
        data={makeSavings({
          measured_saved_input_tokens: 1_000_000,
          inferred_saved_input_tokens: 0,
        })}
      />,
    );
    expect(screen.getByText("Agent savings")).toBeInTheDocument();
    expect(screen.queryByText("Estimated agent savings")).not.toBeInTheDocument();
  });

  it("keeps measured and inferred as separate figures", () => {
    render(<SavingsLede data={makeSavings()} />);
    const measured = screen.getByText("Measured").closest("div");
    const inferred = screen.getByText("Inferred").closest("div");

    expect(within(measured as HTMLElement).getByText("250K")).toBeInTheDocument();
    expect(within(inferred as HTMLElement).getByText("750K")).toBeInTheDocument();
  });

  it("says the dollar figure covers only the priced part", () => {
    render(<SavingsLede data={makeSavings()} />);
    // 600K of 1.0M carries no rate, so the value must not read as covering
    // the whole total.
    expect(screen.getByText(/600K of those tokens carry no rate/)).toBeInTheDocument();
    expect(screen.getByText("on 400K of 1.0M")).toBeInTheDocument();
  });

  it("claims full coverage only when nothing is unpriced", () => {
    render(
      <SavingsLede
        data={makeSavings({
          priced_saved_input_tokens: 1_000_000,
          unpriced_saved_input_tokens: 0,
        })}
      />,
    );
    expect(screen.getByText(/values the whole total/)).toBeInTheDocument();
    expect(screen.getByText("on all savings")).toBeInTheDocument();
  });

  it("keeps the coverage date and methodology link beside the total", () => {
    render(<SavingsLede data={makeSavings()} methodologyHref="/method" />);
    // This is the line that survives the reset notice being dismissed.
    expect(screen.getByText(/Measured since Sep 1, 2026\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Method and limits" })).toHaveAttribute(
      "href",
      "/method",
    );
  });

  it("states the window instead of a start date when one is set", () => {
    render(<SavingsLede data={makeSavings({ window_days: 30 })} />);
    expect(screen.getByText(/Covering the last 30 days\./)).toBeInTheDocument();
    expect(screen.queryByText(/Measured since/)).not.toBeInTheDocument();
  });

  it("omits the date rather than printing one it cannot parse", () => {
    render(<SavingsLede data={makeSavings({ first_event_at: "not a date" })} />);
    expect(screen.queryByText(/Measured since/)).not.toBeInTheDocument();
  });

  it("counts interactions, not tokens, under the interaction figure", () => {
    render(<SavingsLede data={makeSavings()} />);
    // "90 saved tokens" beside a token total reads as ninety tokens.
    expect(screen.getByText("90 produced a saving")).toBeInTheDocument();
  });

  it("shows no share percentages when the total is zero", () => {
    render(
      <SavingsLede
        data={makeSavings({
          saved_input_tokens: 0,
          measured_saved_input_tokens: 0,
          inferred_saved_input_tokens: 0,
        })}
      />,
    );
    // "0% of total" would be a claim about a distribution that does not exist.
    expect(screen.queryByText(/% of total/)).not.toBeInTheDocument();
  });
});

describe("SavingsSourceTable", () => {
  const rows = [
    { label: "MCP", events: 50, savedInputTokens: 750_000 },
    { label: "Distill", events: 10, savedInputTokens: 250_000 },
  ];

  it("renders each source with its own share of the page total", () => {
    render(
      <SavingsSourceTable
        rows={rows}
        total={1_000_000}
        nameHeader="Surface"
        caption="Savings by surface"
      />,
    );
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
  });

  it("normalises to the passed total, not to the largest row", () => {
    // The caller passes the report total so two tables on one page stay
    // comparable. Normalising per table would render this row as 100%.
    render(
      <SavingsSourceTable
        rows={[{ label: "MCP", events: 1, savedInputTokens: 250_000 }]}
        total={1_000_000}
        nameHeader="Surface"
        caption="Savings by surface"
      />,
    );
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.queryByText("100%")).not.toBeInTheDocument();
  });

  it("does not round a contributing row down to nothing", () => {
    render(
      <SavingsSourceTable
        rows={[{ label: "Hooks", events: 1, savedInputTokens: 100 }]}
        total={1_000_000}
        nameHeader="Surface"
        caption="Savings by surface"
      />,
    );
    // 0.01% is real but not "0%": a source that contributed must not read as
    // one that did not.
    expect(screen.getByText("<1%")).toBeInTheDocument();
  });

  it("renders a caption for a reader who cannot see the section heading", () => {
    render(
      <SavingsSourceTable
        rows={rows}
        total={1_000_000}
        nameHeader="Surface"
        caption="Savings by the surface that produced them"
      />,
    );
    expect(
      screen.getByRole("table", { name: "Savings by the surface that produced them" }),
    ).toBeInTheDocument();
  });

  it("renders the empty node instead of a headed table with no rows", () => {
    render(
      <SavingsSourceTable
        rows={[]}
        total={0}
        nameHeader="Surface"
        caption="Savings by surface"
        empty={<p>No savings carry a surface.</p>}
      />,
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("No savings carry a surface.")).toBeInTheDocument();
  });
});

describe("OpportunityList", () => {
  it("renders no buttons: every row is guidance", () => {
    render(
      <OpportunityList
        items={buildOpportunities(makeSavings(), { distillDocsHref: "/distill" })}
      />,
    );
    // The previous surface styled a docs link as an in-product control. A row
    // here may link; it may never look like something that acts.
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders a guidance link as an ordinary link", () => {
    render(
      <OpportunityList
        items={buildOpportunities(makeSavings(), { distillDocsHref: "/distill" })}
      />,
    );
    expect(
      screen.getByRole("link", { name: "See the distillation setup guide" }),
    ).toHaveAttribute("href", "/distill");
  });

  it("omits the link entirely when no docs href is supplied", () => {
    render(<OpportunityList items={buildOpportunities(makeSavings())} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("labels potential as an opportunity, never as a saving", () => {
    render(
      <OpportunityList items={buildOpportunities(makeSavings())} />,
    );
    expect(
      screen.getByText("Observed opportunity: ~96K tokens"),
    ).toBeInTheDocument();
    expect(screen.getByText("Potentially avoid ~635 tokens")).toBeInTheDocument();
  });

  it("describes re-reads as observed, without claiming what would have replaced them", () => {
    render(<OpportunityList items={buildOpportunities(makeSavings())} />);
    expect(screen.getByText(/were observed\./)).toBeInTheDocument();
    expect(screen.queryByText(/would have replaced/)).not.toBeInTheDocument();
  });

  it("keeps a ledger opportunity kind grammatical whatever it is called", () => {
    render(
      <OpportunityList
        items={buildOpportunities(
          makeSavings({
            missed_events: 0,
            reread_events: 0,
            per_opportunity_kind: [
              {
                kind: "hook_replacement_declined",
                observations: 12,
                estimated_potential_input_tokens: 41_000,
              },
            ],
          }),
        )}
      />,
    );
    // Kinds are an open vocabulary off the ledger, so counting in front of
    // the slug produces "12 hook replacement declined".
    expect(screen.getByText("Hook replacement declined")).toBeInTheDocument();
    expect(screen.getByText(/Observed 12 times/)).toBeInTheDocument();
    expect(screen.queryByText("12 hook replacement declined")).not.toBeInTheDocument();
  });

  it("renders nothing when nothing was observed", () => {
    const { container } = render(
      <OpportunityList
        items={buildOpportunities(
          makeSavings({ missed_events: 0, reread_events: 0, per_opportunity_kind: [] }),
        )}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("agrees with the singular case", () => {
    render(
      <OpportunityList
        items={buildOpportunities(
          makeSavings({ missed_events: 1, reread_events: 1, missed_window_days: 1 }),
        )}
      />,
    );
    expect(screen.getByText("1 command bypassed distillation")).toBeInTheDocument();
    expect(screen.getByText("1 unchanged-file re-read")).toBeInTheDocument();
    expect(screen.getByText(/last 1 day\b/)).toBeInTheDocument();
    expect(screen.getByText(/One full re-read .* was observed\./)).toBeInTheDocument();
  });
});

describe("SavingsTimeline", () => {
  it("summarises the series in text beside the chart", () => {
    render(<SavingsTimeline days={makeSavings().per_day} />);
    expect(
      screen.getByText(/4 days recorded a saving between 2026-09-16 and 2026-09-19\./),
    ).toBeInTheDocument();
    expect(screen.getByText(/largest was 400K tokens on 2026-09-19/)).toBeInTheDocument();
  });

  it("declines to draw a chart from too few points", () => {
    const sparse = makeSavings().per_day.slice(0, MIN_DAYS_FOR_CHART - 1);
    render(<SavingsTimeline days={sparse} />);
    // Three bars in a wide empty box is decoration, not a distribution.
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText(/3 days recorded a saving/)).toBeInTheDocument();
  });

  it("ignores days that recorded nothing", () => {
    render(
      <SavingsTimeline
        days={[
          { group: "2026-09-16", events: 0, saved_input_tokens: 0 },
          { group: "2026-09-17", events: 10, saved_input_tokens: 100 },
        ]}
      />,
    );
    expect(screen.getByText(/1 day recorded a saving/)).toBeInTheDocument();
  });

  it("does not render a single day as a range between itself", () => {
    render(
      <SavingsTimeline
        days={[{ group: "2026-09-18", events: 4, saved_input_tokens: 3169 }]}
      />,
    );
    expect(screen.getByText("1 day recorded a saving on 2026-09-18.")).toBeInTheDocument();
    expect(screen.queryByText(/between 2026-09-18 and 2026-09-18/)).not.toBeInTheDocument();
    // "The largest was" needs something to be largest than.
    expect(screen.queryByText(/largest/)).not.toBeInTheDocument();
  });

  it("says so when no day recorded a saving", () => {
    render(<SavingsTimeline days={[]} />);
    expect(screen.getByText("No day in this window recorded a saving.")).toBeInTheDocument();
  });
});

describe("SpendSummary", () => {
  const spend: SpendView = {
    total_cost_usd: 4.2,
    total_calls: 310,
    total_input_tokens: 1_200_000,
    total_output_tokens: 90_000,
    since: "2026-08-01T00:00:00Z",
  };

  it("names the whole of the model work, not just indexing", () => {
    render(<SpendSummary spend={spend} />);
    // "Indexing cost" named a subset and counted the whole.
    expect(
      screen.getByText(/indexing, page generation and other model-backed operations/),
    ).toBeInTheDocument();
  });

  it("says spend is never subtracted from savings", () => {
    render(<SpendSummary spend={spend} />);
    expect(screen.getByText(/never subtracted from them/)).toBeInTheDocument();
  });

  it("reports nothing spent rather than a row of zeroes", () => {
    render(
      <SpendSummary
        spend={{ ...spend, total_calls: 0, total_cost_usd: 0 }}
      />,
    );
    expect(screen.getByText(/nothing has been spent/)).toBeInTheDocument();
  });
});

describe("UsageDetails", () => {
  it("offers one tab per dimension of the same dataset", () => {
    render(<UsageDetails data={makeSavings()} />);
    for (const name of [/By day/, /Operations/, /Models/, /Agents/]) {
      expect(screen.getByRole("tab", { name })).toBeInTheDocument();
    }
  });

  it("switches dimension without changing scope", () => {
    render(<UsageDetails data={makeSavings()} />);
    fireEvent.click(screen.getByRole("tab", { name: /Operations/ }));
    expect(screen.getByRole("table", { name: /by the operation/ })).toBeInTheDocument();
  });

  it("names the unpriced model bucket instead of dropping it", () => {
    render(<UsageDetails data={makeSavings()} />);
    fireEvent.click(screen.getByRole("tab", { name: /Models/ }));
    // The null-model bucket is how much saving carries no rate evidence.
    expect(screen.getByText("No rate recorded")).toBeInTheDocument();
  });

  it("falls back to the agent slug when the registry resolved no display name", () => {
    render(<UsageDetails data={makeSavings()} />);
    fireEvent.click(screen.getByRole("tab", { name: /Agents/ }));
    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    expect(screen.getByText("codex")).toBeInTheDocument();
  });

  it("carries no count on a dimension with no rows", () => {
    render(<UsageDetails data={makeSavings({ per_model: [] })} />);
    const tab = screen.getByRole("tab", { name: /Models/ });
    // A zero badge reads as a reported count rather than an empty dimension.
    expect(tab).not.toHaveTextContent("0");
  });

  it("explains an empty dimension instead of rendering an empty table", () => {
    render(<UsageDetails data={makeSavings({ per_model: [] })} />);
    fireEvent.click(screen.getByRole("tab", { name: /Models/ }));
    expect(screen.getByText("No savings in this window carry a model.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("can be driven by the host", () => {
    const onValueChange = vi.fn();
    render(
      <UsageDetails data={makeSavings()} value="model" onValueChange={onValueChange} />,
    );
    expect(screen.getByText("No rate recorded")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Agents/ }));
    expect(onValueChange).toHaveBeenCalledWith("agent");
    // Controlled: the selection does not move until the host moves it.
    expect(screen.getByText("No rate recorded")).toBeInTheDocument();
  });
});

describe("SavingsResetNotice", () => {
  it("says what restarted and what was kept", () => {
    render(<SavingsResetNotice onDismiss={vi.fn()} />);
    expect(screen.getByText("Savings accounting has been upgraded.")).toBeInTheDocument();
    expect(screen.getByText(/were not deleted/)).toBeInTheDocument();
  });

  it("never calls itself a cost reset", () => {
    render(<SavingsResetNotice onDismiss={vi.fn()} />);
    // No money moved and no spend record changed.
    expect(screen.getByRole("status").textContent ?? "").not.toMatch(/cost reset/i);
  });

  it("offers the methodology as an ordinary link", () => {
    render(<SavingsResetNotice onDismiss={vi.fn()} methodologyHref="/method" />);
    expect(screen.getByRole("link", { name: "See what changed" })).toHaveAttribute(
      "href",
      "/method",
    );
  });

  it("hands dismissal to the host", () => {
    const onDismiss = vi.fn();
    render(<SavingsResetNotice onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});

describe("SavingsMethodology", () => {
  it("keeps the working available without leading with it", () => {
    render(<SavingsMethodology />);
    const toggle = screen.getByRole("button", { name: /method and limits/i });
    expect(toggle).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByText(/deliberately undersell/)).toBeInTheDocument();
    expect(screen.getByText(/never added to achieved savings/)).toBeInTheDocument();
    expect(screen.getByText(/stays here/)).toBeInTheDocument();
  });
});

describe("surfaceLabel", () => {
  it("names the surfaces the accounting contract defines", () => {
    expect(surfaceLabel("distill")).toBe("Distill");
    expect(surfaceLabel("hook")).toBe("Hooks");
    expect(surfaceLabel("mcp")).toBe("MCP");
    expect(surfaceLabel("vscode_lm")).toBe("VS Code");
  });

  it("shows an unrecognised surface as itself rather than hiding it", () => {
    // A future surface is a real one this build has no word for; collapsing it
    // to "Unknown" would lose which it was.
    expect(surfaceLabel("jetbrains_lm")).toBe("jetbrains_lm");
  });

  it("names an absent surface Unknown", () => {
    expect(surfaceLabel(null)).toBe("Unknown");
  });
});
