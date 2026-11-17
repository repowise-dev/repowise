import { fireEvent, render, screen } from "@testing-library/react";
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
  it("falls back to the size class when there is too little history to name a rhythm", () => {
    render(<StatsReport data={makeData()} />);
    expect(screen.getByText(/acme is a/)).toHaveTextContent("acme is a Village.");
    expect(screen.getByText("17,000")).toBeInTheDocument();
    // 7,000 test lines against 10,000 of source.
    expect(screen.getByText("70")).toBeInTheDocument();
  });

  it("states the index size when the server measured it", () => {
    render(<StatsReport data={{ ...makeData(), index_storage_bytes: 5 * 1024 * 1024 }} />);
    expect(document.body.textContent).toMatch(/Repowise index .* takes 5(\.0)? MB on disk/);
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
    expect(screen.getByRole("columnheader", { name: "Commits, latest 6" })).toBeInTheDocument();
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

  it("links records to their file and commit", () => {
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
    // Name first with the directory clipped; the link still goes to the full path.
    const fileLink = screen.getAllByRole("link").find((a) => a.getAttribute("href") === `/files/${path}`);
    expect(fileLink).toHaveTextContent("module.py");
    expect(screen.getByRole("link", { name: "Big one" })).toHaveAttribute(
      "href",
      "/commits?commit=abc123",
    );
  });
});

function matrix(): number[][] {
  // Weekend-heavy so the repo earns "Weekend Workshop"; the peak is Sat 11am.
  return Array.from({ length: 7 }, (_, wd) =>
    Array.from({ length: 24 }, (_, h) => (h < 8 ? 0 : wd >= 5 ? 4 : 3) + (wd === 5 && h === 11 ? 20 : 0)),
  );
}

function payload(): StatsHighlights {
  const m = matrix();
  const total = m.flat().reduce((a, b) => a + b, 0);
  return {
    repo: { id: "r", name: "demo" },
    scale: {
      file_count: 7341,
      symbol_count: 94946,
      module_count: 8,
      total_nloc: 1_259_713,
      test_nloc: 400_000,
      language_count: 2,
      languages: [
        { language: "python", file_count: 71 },
        { language: "typescript", file_count: 29 },
      ],
      size_class: { name: "Megalopolis", blurb: "A vast megalopolis.", nloc: 1_259_713 },
    },
    origin: {
      first_commit_at: "2026-03-23T10:00:00Z",
      first_commit_author: "Founder",
      first_commit_subject: "Initial commit",
      last_commit_at: "2026-11-13T10:00:00Z",
      age_days: 235,
      total_commits: 4061,
      total_merge_commits: 1,
      contributor_count: 268,
    },
    churn: { lines_added: 2_305_029, lines_deleted: 504_000, net: 1_801_029, deleted_per_hundred: 21.9 },
    rhythm: {
      window: { commits: 4061, first_at: "2026-03-23T10:00:00Z", last_at: "2026-11-13T10:00:00Z", complete: true },
      punch_card: { matrix: m, peak: { weekday: 5, hour: 11, count: 26 }, busiest_weekday: 5, peak_hour: 11, total, timezone_mode: "author_local" },
      velocity: { recent_90d: 16, prior_90d: 7, pct_change: 128.6 },
      busiest_month: { month: "2026-10", total: 1648 },
      busiest_day: { date: "2026-10-14", commits: 143 },
      longest_streak: { days: 61, start: "2026-07-07", end: "2026-09-05" },
      longest_silence: { hours: 312, start: "2026-04-13", end: "2026-04-26" },
      active_days: 198,
      code_half_life_days: 25,
    },
    people: {
      owner_count: 102,
      contributor_count: 268,
      tracked_files: 7164,
      single_owner_files: 5344,
      silo_count: 7,
      truck_factor: 1,
      chronotypes: [],
      arrivals: [
        { name: "Founder", first_commit_at: "2026-03-23T10:00:00Z" },
        { name: "Second", first_commit_at: "2026-03-29T10:00:00Z" },
      ],
    },
    records: {
      largest_file: { path: "src/big.py", nloc: 3293 },
      symbol_shape: { total: 94955, async_count: 10350, async_pct: 10.9, documented_count: 32575, documented_pct: 34.3 },
    },
  };
}

describe("StatsReport instrument panel", () => {
  it("leads with the archetype headline and its evidence", () => {
    render(<StatsReport data={payload()} />);
    expect(screen.getByText(/demo ships like a/)).toHaveTextContent("demo ships like a Weekend Workshop.");
  });

  it("drops the ownership ribbon and the totals other pages own", () => {
    const { container } = render(<StatsReport data={payload()} />);
    const text = container.textContent ?? "";
    for (const gone of ["Truck factor", "Single-owner files", "Knowledge silos", "Owners", "94,946", "7,341"]) {
      expect(text).not.toContain(gone);
    }
  });

  it("keeps precision honest", () => {
    const { container } = render(<StatsReport data={payload()} />);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\d+\.\d%/);
    // 16 against 7 commits is stated, not turned into a percentage.
    expect(text).toContain("up from 7 the 90 days before");
    expect(screen.getByTitle("1,259,713")).toHaveTextContent("1.26M");
    // The async share is of all symbols, not of the documented ones.
    expect(text).toContain("11% of all symbols are async");
    expect(text).not.toContain("of them are async");
  });

  it("reads an hour on tap and from the keyboard, with no fixed minimum width", () => {
    const { container } = render(<StatsReport data={payload()} />);
    expect(container.innerHTML).not.toContain("min-w-[380px]");
    fireEvent.click(container.querySelector('[data-cell="0-9"]')!);
    expect(screen.getByText(/Monday, 9am/).closest("p")).toHaveTextContent("3 commits");
    const lattice = screen.getByRole("group", { name: /arrow keys/ });
    fireEvent.keyDown(lattice, { key: "ArrowRight" });
    expect(screen.getByText(/Monday, 10am/)).toBeInTheDocument();
  });
});
