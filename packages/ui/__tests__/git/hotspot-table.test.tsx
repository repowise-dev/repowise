import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { HotspotTable } from "../../src/git/hotspot-table.js";
import type { Hotspot, HotspotFacets } from "@repowise-dev/types/git";

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

const rows: Hotspot[] = Array.from({ length: 10 }, (_, i) =>
  hotspot({
    file_path: `src/dir/file-${i}.ts`,
    commit_count_90d: 20 - i,
    bus_factor: i % 3,
    rank: i + 41,
    accelerating: i === 0,
  }),
);

const facets: HotspotFacets = { total: 672, bus_factor_1: 378, accelerating: 253, bug_magnet: 0 };

afterEach(() => vi.useRealTimers());

describe("HotspotTable", () => {
  it("shows the server's facet counts as N of total, with no Hot chip", () => {
    render(<HotspotTable hotspots={rows} total={672} facets={facets} onFilterChange={() => {}} />);
    expect(screen.getByRole("button", { name: /^All 672$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Bus factor 1 378 of 672$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Accelerating 253 of 672$/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Hot/ })).not.toBeInTheDocument();
    // A filter with nothing in it is not offered.
    expect(screen.queryByRole("button", { name: /^Bug magnet/ })).not.toBeInTheDocument();
  });

  it("asks the host to filter rather than filtering the loaded page", () => {
    const onFilterChange = vi.fn();
    render(<HotspotTable hotspots={rows} facets={facets} onFilterChange={onFilterChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^Accelerating/ }));
    expect(onFilterChange).toHaveBeenCalledWith("accelerating");
    expect(screen.getByText("file-9.ts")).toBeInTheDocument();
  });

  it("shows the server rank and the name before its folder", () => {
    render(<HotspotTable hotspots={rows} />);
    expect(screen.getByText("41")).toBeInTheDocument();
    expect(screen.getByText("file-0.ts")).toBeInTheDocument();
    expect(screen.getAllByText("src/dir")).toHaveLength(10);
  });

  it("marks only accelerating rows", () => {
    render(<HotspotTable hotspots={rows} />);
    expect(screen.getAllByLabelText("Accelerating")).toHaveLength(1);
  });

  it("sorts through the host, flipping the order on the active column", () => {
    const onSortChange = vi.fn();
    render(
      <HotspotTable hotspots={rows} sort={{ key: "commits", order: "desc" }} onSortChange={onSortChange} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Commits 90d/ }));
    expect(onSortChange).toHaveBeenLastCalledWith({ key: "commits", order: "asc" });
    fireEvent.click(screen.getByRole("button", { name: /^Score/ }));
    expect(onSortChange).toHaveBeenLastCalledWith({ key: "score", order: "desc" });
  });

  it("reports a settled search, not each keystroke", () => {
    vi.useFakeTimers();
    const onQueryChange = vi.fn();
    render(<HotspotTable hotspots={rows} onQueryChange={onQueryChange} />);
    const box = screen.getByRole("searchbox", { name: /Search hotspots/ });
    fireEvent.change(box, { target: { value: "co" } });
    fireEvent.change(box, { target: { value: "core" } });
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(onQueryChange).toHaveBeenCalledTimes(1);
    expect(onQueryChange).toHaveBeenCalledWith("core");
  });

  it("keeps what was typed when the URL echoes an earlier search", () => {
    vi.useFakeTimers();
    const tree = (query: string) => <HotspotTable hotspots={rows} query={query} onQueryChange={() => {}} />;
    const { rerender } = render(tree(""));
    const box = screen.getByRole("searchbox");
    fireEvent.change(box, { target: { value: "co" } });
    act(() => {
      vi.advanceTimersByTime(300);
    });
    fireEvent.change(box, { target: { value: "core" } });
    rerender(tree("co"));
    expect(box).toHaveValue("core");
    // A change from outside (back button, link) still resets the box.
    rerender(tree("api"));
    expect(box).toHaveValue("api");
  });

  it("keeps the active chip when its count drops to zero", () => {
    render(
      <HotspotTable hotspots={[]} query="x" facets={facets} filter="bug_magnet" onFilterChange={() => {}} />,
    );
    expect(screen.getByRole("button", { name: /^Bug magnet 0 of 672$/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("offers one prompt per row and one for every listed row", () => {
    const onGeneratePrompt = vi.fn();
    const onGenerateBatchPrompt = vi.fn();
    render(
      <HotspotTable
        hotspots={rows}
        total={672}
        onGeneratePrompt={onGeneratePrompt}
        onGenerateBatchPrompt={onGenerateBatchPrompt}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt for these 672" }));
    expect(onGenerateBatchPrompt).toHaveBeenCalledWith(rows);
    fireEvent.click(screen.getByRole("button", { name: /: src\/dir\/file-3\.ts$/ }));
    expect(onGeneratePrompt).toHaveBeenCalledWith(rows[3]);
  });

  it("keeps the toolbar when a search matches nothing", () => {
    render(<HotspotTable hotspots={[]} query="zzz" onQueryChange={() => {}} />);
    expect(screen.getByText("No matches")).toBeInTheDocument();
    expect(screen.getByRole("searchbox")).toHaveValue("zzz");
  });

  it("renders the empty state when there are no hotspots", () => {
    render(<HotspotTable hotspots={[]} />);
    expect(screen.getByText("No hotspots found")).toBeInTheDocument();
  });
});

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

describe("HotspotTable fix history", () => {
  it("hides the Fixes column entirely when no row has counted fixes", () => {
    render(<HotspotTable hotspots={rows} />);
    expect(screen.queryByRole("columnheader", { name: /^Fixes/ })).not.toBeInTheDocument();
  });

  it("shows the Fixes column when fixes sit only past the loaded page", () => {
    render(<HotspotTable hotspots={rows} facets={{ ...facets, with_fixes: 4 }} onSortChange={() => {}} />);
    expect(screen.getByRole("columnheader", { name: /^Fixes/ })).toBeInTheDocument();
  });

  it("shows the Fixes column once the index carries fix data", () => {
    const withFixes = [
      hotspot({ file_path: "src/a.ts", prior_defect_count: 7, last_fix_at: daysAgo(3) }),
      hotspot({ file_path: "src/b.ts", prior_defect_count: 0, primary_owner: "Bo" }),
    ];
    render(<HotspotTable hotspots={withFixes} />);
    expect(screen.getByRole("columnheader", { name: /^Fixes/ })).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    // The file with no fixes shows a dash, not a misleading zero.
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("marks a magnet only when it carries a timestamp", () => {
    const anchored = hotspot({
      file_path: "src/magnet.ts",
      prior_defect_count: 5,
      bug_magnet: true,
      last_fix_at: daysAgo(4),
    });
    const unanchored = hotspot({
      file_path: "src/unanchored.ts",
      prior_defect_count: 5,
      bug_magnet: true,
      last_fix_at: null,
    });
    render(<HotspotTable hotspots={[anchored, unanchored]} />);
    expect(screen.getAllByTitle(/^Bug magnet:/)).toHaveLength(1);
  });
});
