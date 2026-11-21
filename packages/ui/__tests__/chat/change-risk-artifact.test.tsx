import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RiskReportArtifactData } from "@repowise-dev/types/chat";
import { RiskReportRenderer } from "../../src/chat/artifacts";

const finding = {
  id: "chf_abc123",
  change: "introduced" as const,
  dimension: "defect" as const,
  biomarker: "complex_method",
  severity: "high" as const,
  path: "src/app.py",
  symbol: "handler",
  lines: [12, 40] as [number, number],
  reason: "handler has cyclomatic complexity 21",
  attribution: {
    basis: "added_lines",
    confidence: "high" as const,
    why: "Lines 12-40 are added or rewritten by this change.",
  },
  inspect: "get_change_risk(revspec='HEAD', finding_id='chf_abc123')",
};

const data: RiskReportArtifactData = {
  ref: "HEAD",
  diff_shape: "Diff shape: bigger than 82% of this repo's recent commits.",
  risk_percentile: 82,
  review_priority: "Elevated",
  classification: "Higher-risk than most recent commits",
  directive: {
    status: "review_required",
    headline: "1 new finding needs review, starting with complex_method in src/app.py.",
    reasons: ["high defect: complex_method in handler (added_lines)"],
    next_actions: ["Inspect src/app.py:12 (chf_abc123)", "Run: tests/test_app.py"],
  },
  health_delta: {
    status: "available",
    explanation: "Compared 3 changed files on both sides.",
    basis: "both_sides_analyzed",
    introduced: 1,
    worsened: 0,
    resolved: 2,
    top_findings: [finding],
    findings_total: 1,
    findings_emitted: 1,
    scope: { changed: 3, eligible: 3, analyzed: 3, skipped: 0, failed: 0 },
  },
  impacted_tests: { tests_to_run: ["tests/test_app.py"], status: "selected", run_all: false },
};

describe("change risk artifact", () => {
  it("leads with the verdict and what the change made worse", () => {
    render(<RiskReportRenderer data={data} />);

    expect(screen.getByText("Review required")).toBeInTheDocument();
    expect(screen.getByText(/1 new finding needs review/)).toBeInTheDocument();
    expect(screen.getByText("What this change made worse")).toBeInTheDocument();
    expect(
      screen.getByText("handler has cyclomatic complexity 21"),
    ).toBeInTheDocument();
  });

  it("shows each finding's attribution, not just its location", () => {
    render(<RiskReportRenderer data={data} />);

    expect(
      screen.getByText(/Lines 12-40 are added or rewritten by this change/),
    ).toBeInTheDocument();
    expect(screen.getByText(/high confidence/)).toBeInTheDocument();
  });

  it("names severity in words via the shared SeverityMark, not colour alone", () => {
    render(<RiskReportRenderer data={data} />);

    // The finding cards lead; the disclosure lists come after them.
    const card = screen.getAllByRole("listitem")[0]!;
    expect(within(card).getByText("High")).toBeInTheDocument();
    expect(within(card).getByText(/introduced · defect/)).toBeInTheDocument();
  });

  it("surfaces historically fragile files in the context row", () => {
    render(
      <RiskReportRenderer
        data={{
          ...data,
          fix_history: {
            available: true,
            files: [{ path: "src/app.py", churn: 42, fix_pressure: 9.1 }],
          },
        }}
      />,
    );

    expect(screen.getByText("Fragile")).toBeInTheDocument();
    expect(screen.getByText("Historically fragile files")).toBeInTheDocument();
    expect(screen.getByText("42 changes")).toBeInTheDocument();
  });

  it("states the diff shape in words, with no raw score", () => {
    render(<RiskReportRenderer data={data} />);

    // The ranked reading stays in the compact context row...
    expect(screen.getByText("p82")).toBeInTheDocument();
    // ...and the sentence replaces the raw 0-10 the renderer used to show.
    const disclosure = screen.getByText("More detail");
    expect(disclosure.closest("details")).not.toBeNull();
    expect(screen.getByText(/bigger than 82%/)).toBeInTheDocument();
    expect(screen.queryByText("Diff-shape score")).toBeNull();
  });

  it("announces a partial comparison as a live status, not a clean result", () => {
    render(
      <RiskReportRenderer
        data={{
          ...data,
          directive: {
            status: "unknown",
            headline: "Nothing new in what was compared, but part was not analysed.",
          },
          health_delta: {
            ...data.health_delta!,
            status: "partial",
            explanation: "Compared 2 of 3 changed files; 1 was not analysed.",
            top_findings: [],
            introduced: 0,
            findings_total: 0,
            findings_emitted: 0,
            skipped: { total: 1, by_reason: { not_health_analyzable: 1 } },
          },
        }}
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Compared 2 of 3 changed files");
    expect(screen.getByText("Not established")).toBeInTheDocument();
    expect(screen.queryByText("What this change made worse")).toBeNull();
  });

  it("says every test must run, and why, when no subset is vouched for", () => {
    const reason = "package.json changed: build or test configuration can change any test.";
    render(
      <RiskReportRenderer
        data={{
          ...data,
          impacted_tests: {
            status: "run_all",
            run_all: true,
            reasons: [reason],
            tests_to_run: ["tests/test_app.py"],
          },
        }}
      />,
    );

    expect(screen.getByText("Run every test")).toBeInTheDocument();
    expect(screen.getByText(`Run every test: ${reason}`)).toBeInTheDocument();
    expect(screen.getByText("Run these first")).toBeInTheDocument();
    expect(screen.queryByText("1 to run")).not.toBeInTheDocument();
  });

  it("shows each changed file's score on both sides, and the average", () => {
    render(
      <RiskReportRenderer
        data={{
          ...data,
          health_delta: {
            ...data.health_delta!,
            score_basis: "structure_only",
            files: [
              {
                path: "src/app.py",
                base_score: 6.2,
                head_score: 4.9,
                base_structure: 3.8,
                head_structure: 5.1,
                nloc_base: 120,
                nloc_head: 132,
              },
              {
                path: "src/old.py",
                base_score: 7.4,
                head_score: null,
                base_structure: 2.6,
                head_structure: null,
                nloc_base: 40,
                nloc_head: null,
                moved_to: ["src/new.py"],
              },
              {
                path: "src/new.py",
                base_score: null,
                head_score: 7.4,
                base_structure: null,
                head_structure: 2.6,
                nloc_base: null,
                nloc_head: 40,
              },
            ],
            files_total: 3,
            score_change: {
              base: 6.2,
              head: 4.9,
              delta: -1.3,
              structure_delta: 1.3,
              weighting: "base_nloc",
              files_compared: 3,
            },
          },
        }}
      />,
    );

    expect(screen.getByText("Health")).toBeInTheDocument();
    expect(screen.getByText("6.2 to 4.9")).toBeInTheDocument();
    expect(screen.getByText("Files by score change")).toBeInTheDocument();
    expect(screen.getByText("src/app.py")).toBeInTheDocument();
    expect(screen.getByText("6.2 → 4.9")).toBeInTheDocument();
    // A deleted file keeps its base score and says where its code went.
    expect(screen.getByText("deleted → src/new.py")).toBeInTheDocument();
    expect(screen.getByText("new, 7.4")).toBeInTheDocument();
  });

  it("says how many of the changed files were scored when the list is capped", () => {
    render(
      <RiskReportRenderer
        data={{
          ...data,
          health_delta: {
            ...data.health_delta!,
            files: [
              {
                path: "src/app.py",
                base_score: 6.2,
                head_score: 4.9,
                base_structure: 3.8,
                head_structure: 5.1,
                nloc_base: 120,
                nloc_head: 132,
              },
            ],
            files_total: 14,
          },
        }}
      />,
    );

    expect(screen.getByText("Files scored")).toBeInTheDocument();
    expect(screen.getByText("1 of 14")).toBeInTheDocument();
    // No average was produced, so the context row stays empty.
    expect(screen.queryByText("Health")).toBeNull();
  });

  it("renders an older server's delta with no per-file scores unchanged", () => {
    render(<RiskReportRenderer data={data} />);

    expect(screen.getByText("More detail")).toBeInTheDocument();
    expect(screen.queryByText("Files by score change")).toBeNull();
    expect(screen.queryByText("Health")).toBeNull();
  });

  it("reports an error with an alert role", () => {
    render(<RiskReportRenderer data={{ error: "Could not read change 'nope'." }} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Could not read change");
  });

  it("still renders a legacy payload that has no delta", () => {
    render(
      <RiskReportRenderer
        data={{ ref: "HEAD", risk_percentile: 40, review_priority: "Normal" }}
      />,
    );

    expect(screen.getByText("p40")).toBeInTheDocument();
    expect(screen.getByText("Normal")).toBeInTheDocument();
  });
});
