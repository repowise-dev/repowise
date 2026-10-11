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

describe("GitHistoryPanel age", () => {
  it("renders a known age", () => {
    render(<GitHistoryPanel git={meta({ age_days: 400 })} />);
    expect(screen.getByText("1 year 1 month")).toBeTruthy();
  });

  it("keeps '< 1 day' for a file that really is new", () => {
    render(<GitHistoryPanel git={meta({ age_days: 0, commit_count_total: 1 })} />);
    expect(screen.getByText("< 1 day")).toBeTruthy();
  });

  it("drops the row for a file whose history never got indexed", () => {
    // Indexing that timed out persists column defaults, so an unindexed file
    // and a file created today are both age 0. Only one of them has commits.
    render(<GitHistoryPanel git={meta({ age_days: 0, commit_count_total: 0 })} />);
    expect(screen.queryByText("Age")).toBeNull();
    expect(screen.queryByText("< 1 day")).toBeNull();
  });

  it("does not print NaN when the payload omits the age entirely", () => {
    const { age_days: _drop, ...rest } = meta();
    render(<GitHistoryPanel git={rest as GitMetadata} />);
    expect(screen.queryByText(/NaN/)).toBeNull();
  });
});

describe("GitHistoryPanel author share", () => {
  const authors = [
    { name: "Ada", email: "ada@example.com", commit_count: 30 },
    { name: "Grace", email: "grace@example.com", commit_count: 10 },
  ];

  it("derives the share from commit counts when the rows carry no pct", () => {
    // The file-detail endpoint serves these rows straight off the artifact,
    // which has counts and no share at all.
    render(<GitHistoryPanel git={meta({ top_authors: authors, commit_count_total: 40 })} />);
    expect(screen.getByText("75%")).toBeTruthy();
    expect(screen.getByText("25%")).toBeTruthy();
    expect(screen.queryByText(/NaN/)).toBeNull();
  });

  it("prefers a served pct over the derived one", () => {
    render(
      <GitHistoryPanel
        git={meta({
          top_authors: [{ ...authors[0]!, pct: 0.5 }],
          commit_count_total: 40,
        })}
      />,
    );
    expect(screen.getByText("50%")).toBeTruthy();
  });

  it("falls back to the raw count when there is no denominator", () => {
    render(
      <GitHistoryPanel
        git={meta({ top_authors: [{ ...authors[0]!, commit_count: 0 }], commit_count_total: 0 })}
      />,
    );
    expect(screen.getByText("0 commits")).toBeTruthy();
    expect(screen.queryByText(/NaN/)).toBeNull();
  });
});
