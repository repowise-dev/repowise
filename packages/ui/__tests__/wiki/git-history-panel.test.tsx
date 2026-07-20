import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { GitHistoryPanel } from "../../src/wiki/git-history-panel.js";
import type { GitMetadata } from "@repowise-dev/types/git";

const daysAgo = (n: number) =>
  new Date(Date.now() - n * 86_400_000).toISOString();

const meta = (overrides: Partial<GitMetadata> = {}): GitMetadata => ({
  file_path: "src/foo.ts",
  commit_count_total: 40,
  commit_count_90d: 8,
  commit_count_30d: 2,
  first_commit_at: daysAgo(400),
  last_commit_at: daysAgo(3),
  primary_owner_name: "Ada",
  primary_owner_email: "ada@example.com",
  primary_owner_commit_pct: 0.6,
  recent_owner_name: "Ada",
  recent_owner_commit_pct: 0.5,
  top_authors: [],
  significant_commits: [],
  co_change_partners: [],
  is_hotspot: true,
  is_stable: false,
  churn_percentile: 92,
  age_days: 400,
  bus_factor: 2,
  contributor_count: 4,
  lines_added_90d: 100,
  lines_deleted_90d: 40,
  avg_commit_size: 30,
  commit_categories: {},
  merge_commit_count_90d: 0,
  ...overrides,
});

describe("GitHistoryPanel fix history", () => {
  it("puts the fix count beside the churn badges, and agrees with the card below", () => {
    render(<GitHistoryPanel git={meta({ prior_defect_count: 3, last_fix_at: daysAgo(4) })} />);
    // The headline badge and the change-history card's fix row, telling the
    // same story rather than one counting and the other not.
    expect(screen.getAllByText(/3 fixes · last 4d ago/)).toHaveLength(2);
  });

  it("adds nothing to a file with no counted fixes", () => {
    render(<GitHistoryPanel git={meta({ prior_defect_count: 0 })} />);
    expect(screen.queryByText(/fixes/)).toBeNull();
    expect(screen.queryByText(/Bug magnet/)).toBeNull();
  });

  it("flags a bug magnet only with the age beside it", () => {
    const { unmount } = render(
      <GitHistoryPanel
        git={meta({ prior_defect_count: 5, last_fix_at: daysAgo(6), bug_magnet: true })}
      />,
    );
    expect(screen.getByText(/^Bug magnet ·/)).toBeTruthy();
    unmount();

    render(
      <GitHistoryPanel
        git={meta({ prior_defect_count: 5, last_fix_at: null, bug_magnet: true })}
      />,
    );
    expect(screen.queryByText(/Bug magnet/)).toBeNull();
  });

  it("leaves the card's fix row as a bare count when the timestamp is missing", () => {
    render(<GitHistoryPanel git={meta({ prior_defect_count: 2, last_fix_at: null })} />);
    expect(screen.getAllByText(/2 fixes/)).toHaveLength(2);
    expect(screen.queryByText(/· last/)).toBeNull();
  });
});
