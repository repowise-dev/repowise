import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FileHistoryTab } from "../../src/files/file-history-tab.js";
import type { FileDetailGit } from "@repowise-dev/types/files";

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

function makeGit(overrides: Partial<FileDetailGit> = {}): FileDetailGit {
  return {
    file_path: "src/foo.ts",
    commit_count_total: 40,
    commit_count_90d: 8,
    commit_count_30d: 2,
    churn_percentile: 92,
    primary_owner: "Ada",
    is_hotspot: true,
    is_stable: false,
    bus_factor: 2,
    contributor_count: 4,
    lines_added_90d: 100,
    lines_deleted_90d: 40,
    avg_commit_size: 30,
    commit_categories: {},
    significant_commits: [],
    top_authors: [],
    co_change_partners: [],
    agent: { agent_commit_count: 0, agent_authored_pct: null, tier_counts: {} },
    first_commit_at: null,
    ...overrides,
  };
}

const partnerHref = (path: string) => `/files/${path}`;

function renderTab(git: FileDetailGit | null) {
  return render(<FileHistoryTab git={git} linkPrefix="/repos/r1" partnerHref={partnerHref} />);
}

describe("FileHistoryTab fix history", () => {
  it("shows the fix count and its recency, the way the wiki sidebar does", () => {
    renderTab(makeGit({ prior_defect_count: 3, last_fix_at: daysAgo(4), change_entropy_pct: 60 }));
    // The headline badge plus the change-history card's row: same file, same story.
    expect(screen.getAllByText(/3 fixes · last 4d ago/)).toHaveLength(2);
  });

  it("flags a bug magnet only when an age can sit beside it", () => {
    const { unmount } = renderTab(
      makeGit({ prior_defect_count: 5, last_fix_at: daysAgo(6), bug_magnet: true }),
    );
    expect(screen.getByText(/^Bug magnet ·/)).toBeTruthy();
    unmount();

    renderTab(makeGit({ prior_defect_count: 5, last_fix_at: null, bug_magnet: true }));
    expect(screen.queryByText(/Bug magnet/)).toBeNull();
    expect(screen.getAllByText(/5 fixes/)).toHaveLength(2);
  });

  it("adds nothing at all to a file the index carries no fix data for", () => {
    renderTab(makeGit());
    expect(screen.queryByText(/fix/i)).toBeNull();
    expect(screen.queryByText(/Change Complexity/i)).toBeNull();
  });

  it("draws a counted zero but stays silent on an unknown", () => {
    // A file the fix pass examined and found clean still says so, because the
    // card is already on screen for its entropy.
    const { unmount } = renderTab(makeGit({ prior_defect_count: 0, change_entropy_pct: 60 }));
    expect(screen.getByText(/0 fixes/)).toBeTruthy();
    unmount();

    // The tab passes an absent count through as null rather than flattening it
    // to a zero, so a caller that can express "never counted" gets silence.
    renderTab(makeGit({ change_entropy_pct: 60 }));
    expect(screen.queryByText(/fixes/)).toBeNull();
    expect(screen.getByText(/Change entropy/)).toBeTruthy();
  });

  it("carries the rename lineage and the capped-history caveat", () => {
    renderTab(makeGit({ original_path: "src/old-foo.ts", commit_count_capped: true }));
    expect(screen.getByText("src/old-foo.ts")).toBeTruthy();
    expect(screen.getByText(/History capped during indexing/)).toBeTruthy();
  });
});

describe("FileHistoryTab file age", () => {
  it("qualifies the commit total with the span it accumulated over", () => {
    renderTab(makeGit({ age_days: 400 }));
    expect(screen.getByText("over 1 year 1 month")).toBeTruthy();
  });

  it("does not call an unindexed file brand new", () => {
    // Git indexing that timed out persists column defaults, so the file lands
    // with no commits and age 0, and `formatAgeDays(0)` reads "< 1 day".
    renderTab(makeGit({ commit_count_total: 0, age_days: 0 }));
    expect(screen.queryByText(/^over /)).toBeNull();
    expect(screen.queryByText(/< 1 day/)).toBeNull();
  });

  it("says nothing about age when the payload omits it", () => {
    renderTab(makeGit());
    expect(screen.queryByText(/^over /)).toBeNull();
    expect(screen.queryByText(/NaN/)).toBeNull();
  });

  it("marks a stable file", () => {
    renderTab(makeGit({ is_stable: true }));
    expect(screen.getByText("Stable")).toBeTruthy();
  });
});

describe("FileHistoryTab without git", () => {
  it("falls back to the empty state", () => {
    renderTab(null);
    expect(screen.getByText("No git history")).toBeTruthy();
  });
});
