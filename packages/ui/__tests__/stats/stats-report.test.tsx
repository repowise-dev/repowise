import { render, screen } from "@testing-library/react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { describe, expect, it } from "vitest";
import { RecordsList } from "../../src/stats/records-list.js";
import { StatsReport } from "../../src/stats/stats-report.js";

function makeData(overrides: Partial<StatsHighlights["rhythm"]> = {}): StatsHighlights {
  const matrix = Array.from({ length: 7 }, () => Array<number>(24).fill(0));
  matrix[2]![14] = 6;
  return {
    repo: { id: "r1", name: "acme" },
    scale: {
      file_count: 120,
      symbol_count: 900,
      module_count: 4,
      total_nloc: 17_000,
      test_nloc: 7_000,
      language_count: 1,
      languages: [{ language: "python", file_count: 120 }],
      size_class: { name: "Village", blurb: "A tidy village.", nloc: 17_000 },
    },
    origin: {
      first_commit_at: "2024-01-01T00:00:00+00:00",
      first_commit_author: "Ada",
      first_commit_subject: "Initial commit",
      last_commit_at: "2024-06-01T00:00:00+00:00",
      age_days: 152,
      total_commits: 5_000,
      contributor_count: 3,
    },
    churn: null,
    rhythm: {
      window: {
        commits: 6,
        first_at: "2024-05-01T00:00:00+00:00",
        last_at: "2024-06-01T00:00:00+00:00",
        complete: false,
      },
      punch_card: {
        matrix,
        peak: { weekday: 2, hour: 14, count: 6 },
        busiest_weekday: 2,
        peak_hour: 14,
        total: 6,
        timezone_mode: "utc",
      },
      velocity: null,
      busiest_month: null,
      busiest_day: null,
      longest_streak: null,
      longest_silence: null,
      active_days: 3,
      code_half_life_days: null,
      ...overrides,
    },
    people: {
      owner_count: 2,
      contributor_count: 3,
      single_owner_files: 10,
      silo_count: 0,
      truck_factor: 1,
      chronotypes: [],
      arrivals: [{ name: "Bea", first_commit_at: "2024-05-01T00:00:00+00:00" }],
    },
    records: {},
  };
}

describe("StatsReport", () => {
  it("leads with the size figure and the test ratio", () => {
    render(<StatsReport data={makeData()} />);
    expect(screen.getByText("17,000")).toBeInTheDocument();
    expect(screen.getByText("70 lines of tests")).toBeInTheDocument();
  });

  it("names the commit window when it is not the whole history", () => {
    render(<StatsReport data={makeData()} />);
    expect(screen.getByText(/Drawn from the latest 6 of 5,000 commits/)).toBeInTheDocument();
    // Arrivals inside a partial window are first appearances, and say so.
    expect(screen.getByRole("heading", { name: "First seen" })).toBeInTheDocument();
  });

  it("scopes commit records to the sample they came from", () => {
    const data = makeData();
    data.records.biggest_commit = { sha: "a", subject: "Big", lines_changed: 9, files_changed: 1 };
    render(<StatsReport data={data} />);
    expect(screen.getByRole("heading", { name: "Commits, latest 6" })).toBeInTheDocument();
  });

  it("calls a complete history arrivals", () => {
    const data = makeData();
    data.rhythm.window!.complete = true;
    render(<StatsReport data={data} />);
    expect(screen.getByRole("heading", { name: "Arrivals" })).toBeInTheDocument();
    expect(screen.queryByText(/Drawn from the latest/)).not.toBeInTheDocument();
  });
});

describe("RecordsList", () => {
  it("drops the gnarliest file when the most complex symbol already names it", () => {
    render(
      <RecordsList
        records={{
          gnarliest_file: { path: "src/flow.tsx", max_ccn: 90 },
          most_complex_symbol: { name: "Flow", file_path: "src/flow.tsx", complexity: 90 },
        }}
      />,
    );
    expect(screen.queryByText("Gnarliest file")).not.toBeInTheDocument();
    expect(screen.getByText("Flow")).toBeInTheDocument();
  });

  it("links records to their file and commit, with the full path", () => {
    const path = "packages/a/very/deep/directory/structure/that/used/to/be/cut/module.py";
    render(
      <RecordsList
        records={{
          largest_file: { path, nloc: 900 },
          biggest_commit: { sha: "abc123", subject: "Big one", lines_changed: 10, files_changed: 2 },
        }}
        fileHref={(p) => `/files/${p}`}
        commitHref={(sha) => `/commits?commit=${sha}`}
      />,
    );
    // Wrap points after each slash split the text, so match on content, not name.
    const fileLink = screen.getAllByRole("link").find((a) => a.textContent === path);
    expect(fileLink).toHaveAttribute("href", `/files/${path}`);
    expect(screen.getByRole("link", { name: "Big one" })).toHaveAttribute(
      "href",
      "/commits?commit=abc123",
    );
  });
});
