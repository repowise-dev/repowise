import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import {
  BENCHMARK_FEWER_FILES_PCT,
  DEFAULT_SAVINGS_COPY as copy,
  SavingsNudge,
  SavingsPageBody,
  groupByJob,
  jobForOperation,
  type SavingsView,
  type SpendRowView,
  type SpendView,
} from "../../src/savings";

function savings(overrides: Partial<SavingsView> = {}): SavingsView {
  return {
    available: true,
    window_days: null,
    first_event_at: "2026-07-14T00:00:00Z",
    unique_events: 120,
    successful_or_usable_partial_events: 118,
    saved_input_tokens: 2_000_000,
    measured_saved_input_tokens: 1_500_000,
    inferred_saved_input_tokens: 500_000,
    priced_input_savings_usd: 6,
    baseline_events: 100,
    reducing_events: 90,
    baseline_input_tokens: 4_000_000,
    baseline_saved_input_tokens: 2_840_000,
    input_reduction_ratio: 0.71,
    files_not_opened: 1284,
    output_tokens_saved_estimated: 136_000,
    output_usd_saved_estimated: 1.84,
    total_usd_saved_estimated: 7.84,
    per_operation: [
      { group: "git_diff", events: 40, saved_input_tokens: 900_000, priced_input_savings_usd: 2.7 },
      { group: "search_codebase", events: 30, saved_input_tokens: 600_000, priced_input_savings_usd: 1.8 },
      { group: "get_answer", events: 20, saved_input_tokens: 300_000, priced_input_savings_usd: 0.9 },
      { group: "get_risk", events: 10, saved_input_tokens: 200_000, priced_input_savings_usd: 0.6 },
    ],
    per_day: [
      { group: "2026-11-17", events: 50, saved_input_tokens: 800_000, priced_input_savings_usd: 2.4 },
      { group: "2026-11-18", events: 70, saved_input_tokens: 1_200_000, priced_input_savings_usd: 3.6 },
    ],
    files_per_day: [{ group: "2026-11-18", files_not_opened: 1284 }],
    ...overrides,
  };
}

const spend: SpendView = {
  total_cost_usd: 8.95,
  total_calls: 7789,
  total_input_tokens: 18_900_000,
  total_output_tokens: 4_300_000,
  since: null,
};

const rows: SpendRowView[] = [
  { group: "doc_generation", calls: 5904, input_tokens: 14_890_000, output_tokens: 3_300_000, cost_usd: 6.94 },
  { group: "neighbor_rerank", calls: 10, input_tokens: 1000, output_tokens: 100, cost_usd: 0.01 },
];

function renderBody(props: Partial<Parameters<typeof SavingsPageBody>[0]> = {}) {
  return render(
    <SavingsPageBody
      savings={savings()}
      usage={{ summary: spend, rows, grouping: "operation", onGroupingChange: () => {} }}
      {...props}
    />,
  );
}

describe("SavingsPageBody, full report", () => {
  it("leads with the input reduction and the before/after strips", () => {
    renderBody();
    expect(screen.getByText("71%")).toBeTruthy();
    expect(screen.getByText(copy.heroUnit)).toBeTruthy();
    expect(screen.getByText(copy.heroSince("Jul 14, 2026"))).toBeTruthy();
    // Baseline and baseline minus saved, straight off the report.
    expect(screen.getByText("4.0M tokens")).toBeTruthy();
    expect(screen.getByText("1.2M tokens")).toBeTruthy();
    expect(screen.getByText(copy.stripCoverage("90"))).toBeTruthy();
  });

  it("proves it in tokens, files and dollars", () => {
    renderBody();
    expect(screen.getByText("1,284")).toBeTruthy();
    expect(screen.getByText(copy.filesSub(BENCHMARK_FEWER_FILES_PCT))).toBeTruthy();
    expect(screen.getByText("$7.84")).toBeTruthy();
    expect(screen.getByText(copy.billSubOutput("$1.84"))).toBeTruthy();
    expect(screen.getByText(copy.tokensSubOutput("136K"))).toBeTruthy();
  });

  it("groups operations into plain jobs, ranked, with dollars", () => {
    renderBody();
    const names = screen.getAllByText(/Trimmed long|Answered searches|Answered code|Ran risk|Skipped re-reads/);
    expect(names.map((n) => n.textContent)).toEqual([
      "Trimmed long command output",
      "Answered searches",
      "Answered code questions",
      "Ran risk and review checks",
      "Skipped re-reads",
    ]);
    expect(screen.getByText("$2.70")).toBeTruthy();
    expect(screen.getByText(copy.jobNone)).toBeTruthy();
  });

  it("shows Repowise's own spend with plain activity names and the raw key", () => {
    renderBody();
    expect(screen.getByText("Writing docs")).toBeTruthy();
    expect(screen.getByText("doc_generation")).toBeTruthy();
    expect(screen.getByText("Neighbor rerank")).toBeTruthy();
    expect(screen.getAllByText("$8.95").length).toBeGreaterThan(0);
  });

  it("renders the nudge it is given and no other", () => {
    renderBody({ nudge: <p>one nudge</p> });
    expect(screen.getByText("one nudge")).toBeTruthy();
  });
});

describe("files not opened", () => {
  it("dates a count that began inside the window", () => {
    renderBody();
    // per_day starts 2026-11-17, file tracking 2026-11-18.
    expect(screen.getByText(copy.filesSinceShort("Nov 18, 2026"))).toBeTruthy();
    expect(screen.getByText(/skipped 1,284 file opens/)).toBeTruthy();
  });

  it("leaves a small count out of the lede but keeps it in the proof row", () => {
    renderBody({ savings: savings({ files_not_opened: 2 }) });
    expect(screen.queryByText(/file opens/)).toBeNull();
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("does not date a count when tracking covers the whole window", () => {
    renderBody({
      savings: savings({ files_per_day: [{ group: "2026-11-17", files_not_opened: 1284 }] }),
    });
    expect(screen.queryByText(/^since /)).toBeNull();
  });
});

describe("SavingsPageBody, partial states", () => {
  it("falls back to tokens when no call had a baseline, and drops the strips", () => {
    renderBody({ savings: savings({ input_reduction_ratio: null, baseline_input_tokens: 0 }) });
    expect(screen.getByText(copy.heroTokensUnit)).toBeTruthy();
    expect(screen.queryByText(copy.stripBase)).toBeNull();
  });

  it("says why there is no dollar figure instead of printing $0.00", () => {
    renderBody({
      savings: savings({
        priced_input_savings_usd: 0,
        total_usd_saved_estimated: null,
        output_usd_saved_estimated: null,
        per_operation: [{ group: "git_diff", events: 3, saved_input_tokens: 9000, priced_input_savings_usd: 0 }],
      }),
    });
    expect(screen.getByText(copy.billNone)).toBeTruthy();
    expect(screen.queryByText("$0.00")).toBeNull();
    // Jobs show tokens, not dollars, when nothing was priced.
    expect(screen.getAllByText("9K tokens").length).toBeGreaterThan(0);
  });

  it("never prints $0.00 for an unpriced job in a priced window", () => {
    renderBody({
      savings: savings({
        per_operation: [
          { group: "get_answer", events: 5, saved_input_tokens: 9000, priced_input_savings_usd: 1.5 },
          { group: "unknown", events: 3, saved_input_tokens: 4000, priced_input_savings_usd: 0 },
        ],
      }),
    });
    const row = screen.getByText("Trimmed long command output").closest("li") as HTMLElement;
    expect(within(row).getByText("4K tokens")).toBeTruthy();
    expect(within(row).queryByText("$0.00")).toBeNull();
  });

  it("names mixed subscription and local runs without a zero dollar figure", () => {
    renderBody({
      usage: {
        summary: { ...spend, total_cost_usd: 0, total_calls: 5, subscription_calls: 2, local_calls: 1 },
        rows: [
          { group: "answer_synthesis", calls: 3, input_tokens: 1, output_tokens: 1, cost_usd: 0, subscription_calls: 2, local_calls: 1 },
          { group: "doc_generation", calls: 2, input_tokens: 1, output_tokens: 1, cost_usd: 0 },
        ],
        grouping: "operation",
        onGroupingChange: () => {},
      },
    });
    const answers = screen.getByText("Answering questions").closest("tr") as HTMLElement;
    expect(within(answers).getByText(copy.noApiCharges)).toBeTruthy();
    // A metered call that really cost nothing still says so.
    const docs = screen.getByText("Writing docs").closest("tr") as HTMLElement;
    expect(within(docs).getByText("$0.00")).toBeTruthy();
  });

  it("marks a source that is not set up, muted, with no button", () => {
    renderBody({
      savings: savings({ per_operation: [{ group: "get_answer", events: 5, saved_input_tokens: 10 }] }),
      notSetUp: ["trim"],
    });
    const row = screen.getByText("Trimmed long command output").closest("li") as HTMLElement;
    expect(within(row).getByText(copy.jobNotSetUp)).toBeTruthy();
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("says when everything ran on a subscription or the machine", () => {
    renderBody({
      usage: {
        summary: { ...spend, total_cost_usd: 0, total_calls: 4, subscription_calls: 3, local_calls: 1 },
        rows: [
          { group: "answer_synthesis", calls: 3, input_tokens: 1, output_tokens: 1, cost_usd: 0, subscription_calls: 3 },
          { group: "doc_generation", calls: 1, input_tokens: 1, output_tokens: 1, cost_usd: 0, local_calls: 1 },
        ],
        grouping: "operation",
        onGroupingChange: () => {},
      },
    });
    expect(screen.getAllByText(copy.noApiCharges).length).toBeGreaterThan(0);
    expect(screen.getByText(copy.onSubscription)).toBeTruthy();
    expect(screen.getByText(copy.onMachine)).toBeTruthy();
    expect(screen.queryByText("$0.00")).toBeNull();
  });

  it("says Repowise cost nothing when it made no model calls", () => {
    renderBody({
      usage: { summary: { ...spend, total_calls: 0, total_cost_usd: 0 }, rows: [], grouping: "operation", onGroupingChange: () => {} },
    });
    expect(screen.getByText(copy.noModelCalls)).toBeTruthy();
  });

  it("asks for a new grouping through the host", () => {
    const onGroupingChange = vi.fn();
    renderBody({ usage: { summary: spend, rows, grouping: "operation", onGroupingChange } });
    fireEvent.click(screen.getByRole("radio", { name: "Model" }));
    expect(onGroupingChange).toHaveBeenCalledWith("model");
  });
});

describe("SavingsPageBody, empty states", () => {
  it("fresh: nothing recorded and no spend", () => {
    renderBody({
      savings: { available: false },
      usage: { summary: { ...spend, total_calls: 0, total_cost_usd: 0 }, rows: [], grouping: "operation", onGroupingChange: () => {} },
      emptyActions: <button type="button">act</button>,
    });
    expect(screen.getByText(copy.freshTitle)).toBeTruthy();
    expect(screen.getByRole("button", { name: "act" })).toBeTruthy();
    expect(screen.queryByText(copy.usageTitle)).toBeNull();
  });

  it("cost-only: spend but no savings events yet (a host with no savings data)", () => {
    renderBody({ savings: undefined });
    expect(screen.getByText(copy.costOnlyTitle)).toBeTruthy();
    expect(screen.getByText(copy.usageTitle)).toBeTruthy();
    expect(screen.queryByText(copy.heroUnit)).toBeNull();
  });
});

describe("job grouping", () => {
  it("maps known operations and sends unknown ones to answers", () => {
    expect(jobForOperation("git_diff")).toBe("trim");
    expect(jobForOperation("search_digest")).toBe("search");
    expect(jobForOperation("get_change_risk")).toBe("checks");
    expect(jobForOperation("read_reread")).toBe("rereads");
    expect(jobForOperation("unknown")).toBe("trim");
    expect(jobForOperation("some_new_tool")).toBe("answers");
    expect(jobForOperation(null)).toBe("answers");
  });

  it("ranks by dollars when priced, by tokens otherwise, with a stable tiebreak", () => {
    const rows = [
      { group: "get_answer", events: 1, saved_input_tokens: 9000, priced_input_savings_usd: 1 },
      { group: "git_diff", events: 1, saved_input_tokens: 1000, priced_input_savings_usd: 3 },
      { group: "get_why", events: 1, saved_input_tokens: 1000, priced_input_savings_usd: 3 },
    ];
    expect(groupByJob(rows, "usd").map((j) => j.id).slice(0, 3)).toEqual(["trim", "checks", "answers"]);
    expect(groupByJob(rows, "tokens").map((j) => j.id).slice(0, 3)).toEqual(["answers", "trim", "checks"]);
  });

  it("returns every job, empty ones last", () => {
    const jobs = groupByJob([{ group: "get_why", events: 1, saved_input_tokens: 5 }]);
    expect(jobs.map((j) => j.id)).toEqual(["checks", "trim", "search", "answers", "rereads"]);
  });
});

describe("SavingsNudge", () => {
  it("renders the action, links to setup and dismisses", () => {
    const onDismiss = vi.fn();
    render(
      <SavingsNudge
        title="Trim long command output"
        body="Distill isn't on."
        action={<button type="button">Copy prompt</button>}
        href="/repos/r/settings/setup#rewrite"
        linkLabel="Open setup"
        onDismiss={onDismiss}
      />,
    );
    expect(screen.getByRole("link", { name: "Open setup" }).getAttribute("href")).toBe(
      "/repos/r/settings/setup#rewrite",
    );
    fireEvent.click(screen.getByRole("button", { name: copy.nudgeDismiss }));
    expect(onDismiss).toHaveBeenCalled();
  });
});
