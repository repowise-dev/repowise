import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CommitDetail, CommitHealth } from "@repowise-dev/types/git";
import { CommitDetailCard } from "../../src/commits/commit-detail-card";

const commit: CommitDetail = {
  sha: "9e51b06e65c4f28834aa2037a63e082da0ab4cf5",
  short_sha: "9e51b06",
  author_name: "Raghav Chamadiya",
  author_email: "r@example.com",
  committed_at: "2026-09-19T12:00:00Z",
  subject: "stop a failed HEAD read stranding a store's sync pointer",
  lines_added: 137,
  lines_deleted: 12,
  files_changed: 4,
  dirs_changed: 3,
  subsystems_changed: 2,
  entropy: 1.6,
  is_fix: true,
  change_risk_score: 7.9,
  change_risk_level: "high",
  risk_percentile: 36.2,
  review_priority: "moderate",
  drivers: [
    { feature: "la", value: 137, contribution: 1.4, label: "more lines added than baseline" },
    { feature: "nf", value: 4, contribution: -1.1, label: "more files than baseline" },
  ],
};

const files = [
  { path: "cli/commands/update_cmd/command.py", lines_added: 15, lines_deleted: 4, prior_fixes: 35 },
  { path: "core/workspace/update.py", lines_added: 3, lines_deleted: 0, prior_fixes: null },
];

const health: CommitHealth = {
  status: "available",
  introduced_count: 2,
  worsened_count: 1,
  resolved_count: 4,
  files_analyzed: 4,
  files_skipped: 0,
  findings: [
    {
      change_kind: "worsened",
      dimension: "defect",
      biomarker_type: "complex_method",
      severity: "critical",
      severity_before: "high",
      path: "core/workspace/update.py",
      symbol: "sync_store",
      line_start: 42,
      line_end: 130,
      attribution_basis: "added_lines",
      reason: "sync_store has cyclomatic complexity 26",
    },
    {
      change_kind: "introduced",
      dimension: "maintainability",
      biomarker_type: "low_cohesion",
      severity: "medium",
      path: "cli/commands/update_cmd/command.py",
      line_start: 9,
      attribution_basis: "file_change",
      reason: "Updater has low cohesion (LCOM4=2)",
    },
  ],
};

describe("commit detail card", () => {
  it("leads with the repo-relative percentile, not a raw score", () => {
    render(<CommitDetailCard commit={commit} />);

    expect(screen.getByText("36th")).toBeInTheDocument();
    expect(screen.queryByText(/7\.9 out of 10/)).toBeNull();
    expect(screen.queryByText(/Supporting diff-size score/)).toBeNull();
  });

  it("names the files and their bug-fix record from the stored index", () => {
    render(<CommitDetailCard commit={{ ...commit, files }} />);

    // The shared table keeps a table and a stacked-card tree in the DOM.
    expect(screen.getAllByText("cli/commands/update_cmd/command.py").length).toBeGreaterThan(0);
    expect(screen.getAllByText("35").length).toBeGreaterThan(0);
  });

  it("marks an untracked file as unknown rather than as never fixed", () => {
    render(<CommitDetailCard commit={{ ...commit, files }} />);

    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("says so when diff shape and fix history disagree", () => {
    render(<CommitDetailCard commit={commit} fixPercentile={89.4} />);

    expect(screen.getByText(/heavy fix record/)).toBeInTheDocument();
    expect(screen.getByText(/89th percentile/)).toBeInTheDocument();
  });

  it("stays silent about the disagreement when the two rankings agree", () => {
    render(<CommitDetailCard commit={commit} fixPercentile={40} />);

    expect(screen.queryByText(/heavy fix record/)).toBeNull();
  });

  it("renders without the file block on an index that predates it", () => {
    render(<CommitDetailCard commit={commit} />);

    expect(screen.getByText("36th")).toBeInTheDocument();
    expect(screen.queryByText(/Where this change lands/)).toBeNull();
  });

  it("keeps the driver table available but collapsed", () => {
    const { container } = render(<CommitDetailCard commit={commit} />);

    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details?.hasAttribute("open")).toBe(false);
    expect(screen.getByText(/How the diff-shape rank was computed/)).toBeInTheDocument();
  });
  it("counts what the commit broke and what it fixed", () => {
    render(<CommitDetailCard commit={{ ...commit, health }} />);

    expect(screen.getByText(/Introduced or worsened 3 findings/)).toBeInTheDocument();
    expect(screen.getByText(/2 new, 1 made worse/)).toBeInTheDocument();
    expect(screen.getByText(/Resolved 4 existing findings/)).toBeInTheDocument();
  });

  it("shows where each finding landed, worst first", () => {
    render(<CommitDetailCard commit={{ ...commit, health }} />);

    expect(
      screen.getAllByText(/sync_store has cyclomatic complexity 26/).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText(/sync_store — core\/workspace\/update\.py:42/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/was high/).length).toBeGreaterThan(0);
  });

  it("marks a finding the commit only touched, rather than wrote", () => {
    render(<CommitDetailCard commit={{ ...commit, health }} />);

    expect(
      screen.getAllByText(/in a file this commit touched, not on a line it wrote/).length,
    ).toBeGreaterThan(0);
  });

  it("says a clean comparison is clean, rather than staying silent", () => {
    const clean: CommitHealth = {
      ...health,
      introduced_count: 0,
      worsened_count: 0,
      resolved_count: 0,
      findings: [],
    };
    render(<CommitDetailCard commit={{ ...commit, health: clean }} />);

    expect(screen.getByText(/Compared clean/)).toBeInTheDocument();
  });

  it("says how much of a capped list it is showing", () => {
    const capped: CommitHealth = { ...health, introduced_count: 40, worsened_count: 0 };
    render(<CommitDetailCard commit={{ ...commit, health: capped }} />);

    expect(screen.getByText(/Showing the 2 most severe/)).toBeInTheDocument();
  });

  it("names the files it could not analyse", () => {
    const partial: CommitHealth = { ...health, status: "partial", files_skipped: 3 };
    render(<CommitDetailCard commit={{ ...commit, health: partial }} />);

    expect(screen.getByText(/3 of 7 changed files could not be analysed/)).toBeInTheDocument();
  });

  it("puts health above the file list, which the host already shows", () => {
    const { container } = render(<CommitDetailCard commit={{ ...commit, files, health }} />);

    const headings = Array.from(container.querySelectorAll("h2")).map((h) => h.textContent);
    expect(headings).toEqual([
      "What this commit did to code health",
      "Where this change lands",
    ]);
  });

  it("keeps a long file list collapsed so it cannot bury what is below it", () => {
    const many = Array.from({ length: 12 }, (_, i) => ({
      path: `src/f${i}.py`,
      lines_added: 1,
      lines_deleted: 0,
      prior_fixes: null,
    }));
    const { container } = render(<CommitDetailCard commit={{ ...commit, files: many, health }} />);

    const [healthSection, filesSection] = Array.from(container.querySelectorAll("details"));
    expect(healthSection?.open).toBe(true);
    expect(filesSection?.open).toBe(false);
    // Collapsed, it still says how much is behind it.
    expect(screen.getByText("12 files")).toBeInTheDocument();
  });

  it("leaves a short file list open", () => {
    const { container } = render(<CommitDetailCard commit={{ ...commit, files, health }} />);

    // Only the two content sections; the driver breakdown below stays shut.
    const [healthSection, filesSection] = Array.from(container.querySelectorAll("details"));
    expect([healthSection?.open, filesSection?.open]).toEqual([true, true]);
  });

  it("names the headline count on the collapsed health toggle", () => {
    render(<CommitDetailCard commit={{ ...commit, health }} />);

    expect(screen.getByText("3 introduced or worsened")).toBeInTheDocument();
  });

  it("renders no health section for a commit that was never scanned", () => {
    render(<CommitDetailCard commit={commit} />);

    expect(screen.queryByText(/What this commit did to code health/)).toBeNull();
    expect(screen.queryByText(/Compared clean/)).toBeNull();
  });
});
