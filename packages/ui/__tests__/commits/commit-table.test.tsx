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
    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "High priority" })).toBeInTheDocument();
  });
});
