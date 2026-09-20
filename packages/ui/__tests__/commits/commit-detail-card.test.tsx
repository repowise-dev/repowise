import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CommitDetail } from "@repowise-dev/types/git";
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

const fixHistory = {
  percentile: 89.4,
  files: [
    { path: "cli/commands/update_cmd/command.py", churn: 19, fix_pressure: 28.71 },
    { path: "core/workspace/update.py", churn: 4, fix_pressure: 16.2 },
  ],
};

describe("commit detail card", () => {
  it("leads with the repo-relative percentile, not a raw score", () => {
    render(<CommitDetailCard commit={commit} />);

    expect(screen.getByText("36th")).toBeInTheDocument();
    expect(screen.queryByText(/7\.9 out of 10/)).toBeNull();
    expect(screen.queryByText(/Supporting diff-size score/)).toBeNull();
  });

  it("names the files and their bug-fix record when git could score them", () => {
    render(<CommitDetailCard commit={commit} fixHistory={fixHistory} />);

    expect(screen.getByText("cli/commands/update_cmd/command.py")).toBeInTheDocument();
    expect(screen.getByText("28.7")).toBeInTheDocument();
  });

  it("says so when diff shape and fix history disagree", () => {
    render(<CommitDetailCard commit={commit} fixHistory={fixHistory} />);

    expect(screen.getByText(/heavy fix record/)).toBeInTheDocument();
    expect(screen.getByText(/89th percentile/)).toBeInTheDocument();
  });

  it("stays silent about the disagreement when the two rankings agree", () => {
    render(
      <CommitDetailCard
        commit={commit}
        fixHistory={{ ...fixHistory, percentile: 40 }}
      />,
    );

    expect(screen.queryByText(/heavy fix record/)).toBeNull();
  });

  it("renders without the fix block on a server with no checkout", () => {
    render(<CommitDetailCard commit={commit} fixHistory={null} />);

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
});
