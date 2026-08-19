import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import {
  CodeHealthMap,
  MapLegend,
  MapLensSwitcher,
  groupByModule,
  type CodeHealthMapFile,
} from "../../src/health/code-health-map.js";

function f(
  file_path: string,
  nloc: number,
  module: string | null,
  score = 7,
): CodeHealthMapFile {
  return { file_path, nloc, score, module, line_coverage_pct: null, has_test_file: false };
}

// jsdom has no layout engine → stub ResizeObserver so the map can size itself.
beforeAll(() => {
  class RO {
    cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
    }
    observe() {
      this.cb(
        [{ contentRect: { width: 800, height: 600 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
});

describe("groupByModule", () => {
  it("groups files by module, sums NLOC, sorts files biggest-first", () => {
    const galaxies = groupByModule([
      f("a/x.py", 100, "core"),
      f("a/y.py", 40, "core"),
      f("b/z.py", 60, "ui"),
    ]);
    const core = galaxies.find((g) => g.module === "core");
    expect(core?.files).toHaveLength(2);
    expect(core?.totalNloc).toBe(140);
    expect(core?.maxNloc).toBe(100);
    expect(core?.files.map((x) => x.nloc)).toEqual([100, 40]); // desc
    // Galaxies themselves are ordered by total size (core 140 > ui 60).
    expect(galaxies[0]?.module).toBe("core");
  });

  it("drops zero-NLOC files and buckets a null module as (ungrouped)", () => {
    const galaxies = groupByModule([f("a.py", 0, "core"), f("b.py", 20, null)]);
    expect(galaxies.find((g) => g.module === "core")).toBeUndefined();
    expect(galaxies.find((g) => g.module === "(ungrouped)")?.files).toHaveLength(1);
  });
});

describe("CodeHealthMap", () => {
  it("renders the empty state when there are no files", () => {
    const { getByText } = render(<CodeHealthMap files={[]} />);
    expect(getByText(/No files to map yet/i)).toBeInTheDocument();
  });

  it("renders file nodes and opens a file on click", () => {
    const onSelectFile = vi.fn();
    const files = [
      f("core/a.py", 120, "core", 3),
      f("core/b.py", 60, "core", 8),
      f("ui/c.py", 40, "ui", 6),
    ];
    const { container } = render(<CodeHealthMap files={files} onSelectFile={onSelectFile} />);
    // Nodes carry their path on data-path (there is no <title>: it was ~2,000
    // extra nodes driving a native tooltip that duplicated the hover card).
    const target = container.querySelector('circle[data-path="core/a.py"]');
    expect(target).toBeTruthy();
    fireEvent.click(target!);
    expect(onSelectFile).toHaveBeenCalledWith("core/a.py");
  });

  it("draws file nodes without a per-node <title> or non-scaling-stroke", () => {
    const files = [f("core/a.py", 120, "core"), f("core/b.py", 60, "core")];
    const { container } = render(<CodeHealthMap files={files} />);
    // Both were per-frame costs on a ~2,000 element layer that re-rasters
    // through a 460ms zoom transition. Asserted so neither creeps back.
    expect(container.querySelectorAll("title")).toHaveLength(0);
    expect(
      container.querySelectorAll("circle[data-path][vector-effect]"),
    ).toHaveLength(0);
  });

  it("keeps the node stroke at 0.5 device px by pre-dividing by the zoom scale", () => {
    const files = [f("core/a.py", 120, "core"), f("ui/c.py", 40, "ui")];
    const { container } = render(<CodeHealthMap files={files} />);
    const node = () => container.querySelector('circle[data-path="core/a.py"]');
    // Unzoomed, k === 1, so the raw stroke is the target width.
    expect(Number(node()!.getAttribute("stroke-width"))).toBeCloseTo(0.5, 5);

    // Zoom into a galaxy: k > 1, so the user-unit stroke has to shrink by the
    // same factor to land back on 0.5px once the transform scales it up.
    fireEvent.click(container.querySelector("circle[data-galaxy]")!);
    const zoomed = Number(node()!.getAttribute("stroke-width"));
    expect(zoomed).toBeLessThan(0.5);
    expect(zoomed).toBeGreaterThan(0);
  });

  it("zooms into a galaxy and Escape returns to the overview", () => {
    const files = [f("core/a.py", 120, "core"), f("ui/c.py", 40, "ui")];
    const { getByText, queryByText, container } = render(<CodeHealthMap files={files} />);
    // Click a galaxy nebula to focus it.
    const blob = container.querySelector("circle[data-galaxy]");
    expect(blob).toBeTruthy();
    fireEvent.click(blob!);
    expect(getByText("← Overview")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(queryByText("← Overview")).not.toBeInTheDocument();
  });

  it("shows the on-canvas health legend", () => {
    const { getByText } = render(<CodeHealthMap files={[f("a.py", 30, "core")]} />);
    expect(getByText("Health")).toBeInTheDocument();
    expect(getByText(/galaxy = module/i)).toBeInTheDocument();
  });

  it("renders the coverage legend under the coverage lens", () => {
    const { getByText } = render(
      <CodeHealthMap files={[f("a.py", 30, "core")]} overlay="coverage" />,
    );
    // Coverage caption + a coverage-specific legend band identify the lens.
    expect(getByText(/line coverage/i)).toBeInTheDocument();
    expect(getByText("≥80%")).toBeInTheDocument();
  });

  it("performance lens colors by findings + coverage, not the score", () => {
    // Three files: covered-with-findings (heat), covered-clean (green), and an
    // unsupported-language file the perf pass never ran on (grey, NOT green).
    const files: CodeHealthMapFile[] = [
      { ...f("core/hot.py", 120, "core"), performance_findings: 7, performance_analyzed: true },
      { ...f("core/clean.py", 80, "core"), performance_findings: 0, performance_analyzed: true },
      { ...f("core/leveldb.cc", 60, "core"), performance_findings: 0, performance_analyzed: false },
    ];
    const { container, getByText } = render(
      <CodeHealthMap files={files} overlay="performance" />,
    );
    // Educational legend: findings-first, plus the "not analyzed" grey.
    expect(getByText("5+ findings")).toBeInTheDocument();
    expect(getByText("Not analyzed")).toBeInTheDocument();
    expect(getByText("Analyzed, none found")).toBeInTheDocument();

    const fillFor = (path: string) =>
      container.querySelector(`circle[data-path="${path}"]`)?.getAttribute("fill");
    // A file with findings burns red; a covered-clean file is green; an
    // un-analyzed file is grey (tertiary) — never green.
    expect(fillFor("core/hot.py")).toBe("var(--color-error)");
    expect(fillFor("core/clean.py")).toBe("var(--color-success)");
    expect(fillFor("core/leveldb.cc")).toBe("var(--color-text-tertiary)");
  });

  it("fires onOverlayChange when a lens-switch button is clicked", () => {
    const onOverlayChange = vi.fn();
    const { getByRole } = render(
      <CodeHealthMap
        files={[f("a.py", 30, "core")]}
        onOverlayChange={onOverlayChange}
      />,
    );
    // The lens switcher renders one toggle button per lens; click "Maintainability".
    fireEvent.click(getByRole("button", { name: "Maintainability" }));
    expect(onOverlayChange).toHaveBeenCalledWith("maintainability");
  });

  it('offers only the lenses it was given', () => {
    const { getByRole, queryByRole } = render(
      <CodeHealthMap
        files={[f("a.py", 30, "core")]}
        onOverlayChange={vi.fn()}
        lenses={["health", "churn"]}
      />,
    );
    expect(getByRole("button", { name: "Churn" })).toBeInTheDocument();
    // Churn is not a default lens: it colors from a field the host has to join
    // in, so a host that did not join it must not be able to select it.
    expect(queryByRole("button", { name: "Maintainability" })).not.toBeInTheDocument();
  });

  it('chrome="none" renders neither the switcher nor the legend', () => {
    const { queryByText, queryByRole } = render(
      <CodeHealthMap
        files={[f("a.py", 30, "core")]}
        onOverlayChange={vi.fn()}
        chrome="none"
      />,
    );
    expect(queryByRole("button", { name: "Maintainability" })).not.toBeInTheDocument();
    expect(queryByText(/galaxy = module/i)).not.toBeInTheDocument();
  });

  it('defaults to chrome="canvas" so hosts with no other lens picker keep one', () => {
    // The VS Code webview passes onOverlayChange and renders no lens UI of its
    // own; the on-canvas switcher is its only picker.
    const { getByRole } = render(
      <CodeHealthMap files={[f("a.py", 30, "core")]} onOverlayChange={vi.fn()} />,
    );
    expect(getByRole("button", { name: "Maintainability" })).toBeInTheDocument();
  });
});

describe("map chrome, off canvas", () => {
  it("MapLensSwitcher is a radiogroup and reports the picked lens", () => {
    const onOverlayChange = vi.fn();
    const { getByRole } = render(
      <MapLensSwitcher overlay="health" onOverlayChange={onOverlayChange} />,
    );
    expect(getByRole("radiogroup", { name: "Map lens" })).toBeInTheDocument();
    expect(getByRole("radio", { name: "Health" })).toBeChecked();
    fireEvent.click(getByRole("radio", { name: "Performance" }));
    expect(onOverlayChange).toHaveBeenCalledWith("performance");
  });

  it("MapLegend renders the active lens's bands and caption", () => {
    const { getByText } = render(<MapLegend overlay="performance" />);
    expect(getByText("5+ findings")).toBeInTheDocument();
    expect(getByText("Not analyzed")).toBeInTheDocument();
    expect(getByText(/color = findings, not a score/i)).toBeInTheDocument();
  });

  it("MapLegend says it is loading rather than showing bands for absent data", () => {
    const { getByText, queryByText } = render(<MapLegend overlay="churn" loading />);
    expect(getByText(/loading churn/i)).toBeInTheDocument();
    // An all-neutral field must not be captioned as though it were measured.
    expect(queryByText("Top 10%")).not.toBeInTheDocument();
  });
});
