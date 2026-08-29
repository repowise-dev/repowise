import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import {
  CodeHealthMap,
  MapLegend,
  NEUTRAL_FILL,
  MapLensSwitcher,
  groupByModule,
  performanceBurden,
  performanceFill,
  performanceSentence,
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
    fireEvent.keyDown(blob!, { key: "Escape" });
    expect(queryByText("← Overview")).not.toBeInTheDocument();
  });

  it("leaves an Escape aimed at something else alone", () => {
    // The listener used to be on the window, so dismissing anything else on
    // the page - the file drawer this map opens, most of all - also reset the
    // zoom underneath it.
    const files = [f("core/a.py", 120, "core"), f("ui/c.py", 40, "ui")];
    const { getByText, container } = render(<CodeHealthMap files={files} />);
    fireEvent.click(container.querySelector("circle[data-galaxy]")!);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(getByText("← Overview")).toBeInTheDocument();
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

  it("never fills an analyzed-clear file green under the performance lens", () => {
    // A detector that surfaced nothing has proved the absence of a *supported
    // pattern*, not that the file is fast. Green is the strongest reassurance
    // the surface has, and this is its weakest evidence.
    const files: CodeHealthMapFile[] = [
      { ...f("core/hot.py", 120, "core"), performance_opportunities: 7, performance_observations: 12, performance_actionability: "plan_ready", performance_analyzed: true },
      { ...f("core/clean.py", 80, "core"), performance_opportunities: 0, performance_analyzed: true },
      { ...f("core/leveldb.cc", 60, "core"), performance_opportunities: 0, performance_analyzed: false },
    ];
    const { container } = render(<CodeHealthMap files={files} overlay="performance" />);
    const fillFor = (path: string) =>
      container.querySelector(`circle[data-path="${path}"]`)?.getAttribute("fill");
    for (const path of ["core/hot.py", "core/clean.py", "core/leveldb.cc"]) {
      expect(fillFor(path)).not.toBe("var(--color-node-good)");
    }
    // Both take the one neutral. Which of the two a file is cannot be read off
    // a shade of grey, so the field stops trying and the words carry it.
    expect(fillFor("core/clean.py")).toBe(NEUTRAL_FILL);
    expect(fillFor("core/leveldb.cc")).toBe(NEUTRAL_FILL);
  });

  it("carries the burden on the node's own colour, with no second mark", () => {
    const files: CodeHealthMapFile[] = [
      { ...f("core/planned.py", 120, "core"), performance_opportunities: 9, performance_actionability: "plan_ready", performance_analyzed: true },
      { ...f("core/one.py", 100, "core"), performance_opportunities: 1, performance_actionability: "investigate", performance_analyzed: true },
      { ...f("core/clean.py", 80, "core"), performance_opportunities: 0, performance_analyzed: true },
    ];
    const { container } = render(<CodeHealthMap files={files} overlay="performance" />);
    const fillOf = (p: string) =>
      container.querySelector(`circle[data-path="${p}"]`)?.getAttribute("fill");
    expect(fillOf("core/planned.py")).toBe("var(--color-node-critical)");
    expect(fillOf("core/one.py")).toBe("var(--color-node-fair)");
    expect(fillOf("core/clean.py")).toBe(NEUTRAL_FILL);
    // Stored plan earns no mark of its own. It is said in words instead.
    expect(container.querySelectorAll("circle[data-plan]")).toHaveLength(0);
  });

  it("counts observations and says so when the host serves no causal model", () => {
    // A host with raw observations and no read model still gets a lens; it is
    // told which unit it is counting rather than being handed a number under
    // the other one's name.
    const files: CodeHealthMapFile[] = [
      { ...f("core/hot.py", 120, "core"), performance_findings: 4, performance_analyzed: true },
    ];
    expect(performanceBurden(files[0]!)).toEqual({
      state: "investigate",
      count: 4,
      unit: "observations",
    });
    expect(performanceSentence(files[0]!)).toContain("observations");
  });

  it("reports an unknown state when nothing on the row says either way", () => {
    const row = f("core/x.py", 10, "core");
    expect(performanceBurden(row).state).toBe("unknown");
    expect(performanceFill(row)).toBe(NEUTRAL_FILL);
  });

  it("fires onOverlayChange when a lens-switch button is clicked", () => {
    const onOverlayChange = vi.fn();
    const { getByRole } = render(
      <CodeHealthMap
        files={[f("a.py", 30, "core")]}
        onOverlayChange={onOverlayChange}
      />,
    );
    // One switcher implementation, on the canvas and off it, so the two can
    // never disagree about what picking a lens means.
    fireEvent.click(getByRole("radio", { name: "Maintainability" }));
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
    expect(getByRole("radio", { name: "Churn" })).toBeInTheDocument();
    // Churn is not a default lens: it colors from a field the host has to join
    // in, so a host that did not join it must not be able to select it.
    expect(queryByRole("radio", { name: "Maintainability" })).not.toBeInTheDocument();
  });

  it('chrome="none" renders neither the switcher nor the legend', () => {
    const { queryByText, queryByRole } = render(
      <CodeHealthMap
        files={[f("a.py", 30, "core")]}
        onOverlayChange={vi.fn()}
        chrome="none"
      />,
    );
    expect(queryByRole("radio", { name: "Maintainability" })).not.toBeInTheDocument();
    expect(queryByText(/galaxy = module/i)).not.toBeInTheDocument();
  });

  it('defaults to chrome="canvas" so hosts with no other lens picker keep one', () => {
    // The VS Code webview passes onOverlayChange and renders no lens UI of its
    // own; the on-canvas switcher is its only picker.
    const { getByRole } = render(
      <CodeHealthMap files={[f("a.py", 30, "core")]} onOverlayChange={vi.fn()} />,
    );
    expect(getByRole("radio", { name: "Maintainability" })).toBeInTheDocument();
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
    expect(getByText("5 or more")).toBeInTheDocument();
    expect(getByText("Nothing surfaced")).toBeInTheDocument();
    // The caption refuses the runtime claim the colour could be read as making,
    // and the rows are grouped so a flat column of swatches is not the whole key.
    expect(getByText(/never a runtime measurement/i)).toBeInTheDocument();
    expect(getByText("Open causes")).toBeInTheDocument();
    expect(getByText("No open cause")).toBeInTheDocument();
    // Radius is a channel under every lens and was captioned in prose only.
    expect(getByText("lines of code")).toBeInTheDocument();
  });

  it("MapLegend says it is loading rather than showing bands for absent data", () => {
    const { getByText, queryByText } = render(<MapLegend overlay="churn" loading />);
    expect(getByText(/loading churn/i)).toBeInTheDocument();
    // An all-neutral field must not be captioned as though it were measured.
    expect(queryByText("Top 10%")).not.toBeInTheDocument();
  });
});
