import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Commit } from "@repowise-dev/types/git";
import { CommitTable } from "../../src/commits/commit-table";

const emptyCommitFeed: Commit[] = [];

describe("commit table empty states", () => {
  it("explains when the unfiltered repository has no indexed commits", () => {
    render(<CommitTable commits={emptyCommitFeed} sort="date" kind="all" />);

    expect(screen.getByText("No commits indexed")).toBeInTheDocument();
  });

  it("keeps the filter chips visible when an active filter has no matches", () => {
    render(
      <CommitTable
        commits={emptyCommitFeed}
        sort="date"
        kind="high"
        onKindChange={() => undefined}
      />,
    );

    expect(screen.getByText("No matches")).toBeInTheDocument();
    expect(screen.queryByText("No commits indexed")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "All" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "High priority" })).toBeInTheDocument();
  });

  it("renders Size and spread column header and sort option", () => {
    const commitItem: Commit = {
      sha: "9e51b06e65c4f28834aa2037a63e082da0ab4cf5",
      short_sha: "9e51b06",
      author_name: "Dev",
      author_email: "dev@example.com",
      committed_at: "2026-09-19T12:00:00Z",
      subject: "test commit",
      lines_added: 10,
      lines_deleted: 2,
      files_changed: 1,
      dirs_changed: 1,
      subsystems_changed: 1,
      entropy: 0,
      is_fix: false,
      risk_percentile: 50,
      review_priority: "moderate",
    };
    render(<CommitTable commits={[commitItem]} sort="risk" kind="all" />);

    expect(screen.getAllByText("Size and spread").length).toBeGreaterThan(0);
  });
});
