import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { HotspotTable } from "../../src/git/hotspot-table.js";
import type { Hotspot } from "@repowise-dev/types/git";

const hotspot = (overrides: Partial<Hotspot> = {}): Hotspot => ({
  file_path: "src/foo.ts",
  commit_count_90d: 12,
  commit_count_30d: 3,
  churn_percentile: 80,
  temporal_hotspot_score: 4.2,
  primary_owner: "Ada",
  is_hotspot: true,
  is_stable: false,
  bus_factor: 1,
  contributor_count: 2,
  lines_added_90d: 100,
  lines_deleted_90d: 40,
  avg_commit_size: 50,
  commit_categories: {},
  ...overrides,
});

// Small dataset (< 60 rows) so the shared virtualizer renders every row
// unwindowed — assertions stay deterministic under jsdom.
const rows: Hotspot[] = Array.from({ length: 10 }, (_, i) =>
  hotspot({
    file_path: `src/file-${i}.ts`,
    commit_count_90d: 20 - i,
    is_hotspot: i % 2 === 0,
    bus_factor: i % 3,
  }),
);

describe("HotspotTable virtualization", () => {
  it("renders every row in a small dataset", () => {
    render(<HotspotTable hotspots={rows} />);
    for (const h of rows) {
      expect(screen.getByText(h.file_path)).toBeInTheDocument();
    }
  });

  it("memoized filter chips show correct counts", () => {
    render(<HotspotTable hotspots={rows} />);
    // All = 10, Hot = 5 (even indices). The filter chips are <button>s whose
    // accessible name folds in the count span ("All (10)", "Hot (5)").
    expect(screen.getByRole("button", { name: /^All \(10\)$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Hot \(5\)$/ })).toBeInTheDocument();
  });

  it("expands a row to reveal its detail content", () => {
    render(
      <HotspotTable
        hotspots={rows}
        renderExpandedRow={(h) => <div>detail for {h.file_path}</div>}
      />,
    );
    expect(screen.queryByText("detail for src/file-0.ts")).not.toBeInTheDocument();
    const toggle = screen.getAllByRole("button", { name: "Expand symbols" })[0]!;
    fireEvent.click(toggle);
    expect(screen.getByText("detail for src/file-0.ts")).toBeInTheDocument();
  });

  it("filters rows by the active filter chip", () => {
    render(<HotspotTable hotspots={rows} />);
    fireEvent.click(screen.getByRole("button", { name: /^Hot \(5\)$/ }));
    // Only the 5 hot files (even indices) should remain.
    expect(screen.getByText("src/file-0.ts")).toBeInTheDocument();
    expect(screen.queryByText("src/file-1.ts")).not.toBeInTheDocument();
  });

  it("renders the empty state when there are no hotspots", () => {
    render(<HotspotTable hotspots={[]} />);
    expect(screen.getByText("No hotspots found")).toBeInTheDocument();
  });
});
