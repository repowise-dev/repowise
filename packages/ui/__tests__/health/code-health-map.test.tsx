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
import { NODE_RAMP_STOP } from "../../src/health/tokens.js";
import { nodeAt, stubMapEnvironment } from "./map-test-utils.js";

function f(
  file_path: string,
  nloc: number,
  module: string | null,
  score = 7,
): CodeHealthMapFile {
  return { file_path, nloc, score, module, line_coverage_pct: null, has_test_file: false };
}

beforeAll(stubMapEnvironment);

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

  it("opens a file when its node is clicked", () => {
    const onSelectFile = vi.fn();
    const files = [
      f("core/a.py", 120, "core", 3),
      f("core/b.py", 60, "core", 8),
      f("ui/c.py", 40, "ui", 6),
    ];
    const { getByTestId } = render(<CodeHealthMap files={files} onSelectFile={onSelectFile} />);
    fireEvent.click(getByTestId("map-canvas"), nodeAt(files, "core/a.py"));
    expect(onSelectFile).toHaveBeenCalledWith("core/a.py");
  });

  it("draws no element per file", () => {
    // The field paints on a canvas. As SVG it was a circle per file, and each
    // hover, zoom frame and lens switch reconciled every one of them.
    const files = Array.from({ length: 200 }, (_, i) => f(`core/f${i}.py`, 10 + i, "core"));
    const { container } = render(<CodeHealthMap files={files} />);
    expect(container.querySelectorAll("circle")).toHaveLength(0);
    expect(container.querySelectorAll("title")).toHaveLength(0);
    expect(container.querySelectorAll("canvas")).toHaveLength(2);
  });

  it("zooms into a galaxy from its name and Escape returns to the overview", async () => {
    const files = [f("core/a.py", 120, "core"), f("ui/c.py", 40, "ui")];
    const { getByText, queryByText, getByRole, findByRole } = render(<CodeHealthMap files={files} />);
    // Names are placed by the frame loop, and a field this small names all.
    fireEvent.click(await findByRole("button", { name: "core" }));
    expect(getByText("← Overview")).toBeInTheDocument();
    fireEvent.keyDown(getByRole("group", { name: /Code health map/ }), { key: "Escape" });
    expect(queryByText("← Overview")).not.toBeInTheDocument();
  });

  it("leaves an Escape aimed at something else alone", async () => {
    // The listener used to be on the window, so dismissing anything else on
    // the page - the file drawer this map opens, most of all - also reset the
    // zoom underneath it.
    const files = [f("core/a.py", 120, "core"), f("ui/c.py", 40, "ui")];
    const { getByText, findByRole } = render(<CodeHealthMap files={files} />);
    fireEvent.click(await findByRole("button", { name: "core" }));
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(getByText("← Overview")).toBeInTheDocument();
  });

  it("keeps an Escape it used from reaching the drawer behind it", async () => {
    // The file drawer dismisses on a document-level capture listener; one
    // press must not both leave the galaxy and close the drawer.
    const files = [f("core/a.py", 120, "core"), f("ui/c.py", 40, "ui")];
    const { getByRole, findByRole, queryByText } = render(<CodeHealthMap files={files} />);
    fireEvent.click(await findByRole("button", { name: "core" }));
    const drawer = vi.fn();
    document.addEventListener("keydown", drawer, true);
    const field = getByRole("group", { name: /Code health map/ });
    fireEvent.keyDown(field, { key: "Escape" });
    expect(queryByText("← Overview")).not.toBeInTheDocument();
    expect(drawer).not.toHaveBeenCalled();
    // With nothing left to climb out of, the next Escape is the drawer's.
    fireEvent.keyDown(field, { key: "Escape" });
    expect(drawer).toHaveBeenCalledTimes(1);
    document.removeEventListener("keydown", drawer, true);
  });

  it("offers zoom and fit controls with names", () => {
    const { getByRole } = render(<CodeHealthMap files={[f("a.py", 30, "core")]} />);
    for (const name of ["Zoom in", "Zoom out", "Fit the whole field"]) {
      expect(getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("shows the on-canvas health legend on the five canonical bands", () => {
    const { getByText } = render(<CodeHealthMap files={[f("a.py", 30, "core")]} />);
    expect(getByText("Code health")).toBeInTheDocument();
    expect(getByText(/galaxy = module/i)).toBeInTheDocument();
    expect(getByText("Excellent · 8.5+")).toBeInTheDocument();
    expect(getByText("Good · 7.0 to 8.5")).toBeInTheDocument();
    expect(getByText("Fair · 5.5 to 7.0")).toBeInTheDocument();
    expect(getByText("Needs work · 4.0 to 5.5")).toBeInTheDocument();
    expect(getByText("At risk · under 4.0")).toBeInTheDocument();
  });

  it("renders the coverage legend under the coverage lens", () => {
    const { getByText } = render(
      <CodeHealthMap files={[f("a.py", 30, "core")]} overlay="coverage" />,
    );
    expect(getByText(/line coverage/i)).toBeInTheDocument();
    expect(getByText("≥80%")).toBeInTheDocument();
  });

  it("never gives an analyzed-clear file a reassuring tone under the performance lens", () => {
    // A detector that surfaced nothing has proved the absence of a *supported
    // pattern*, not that the file is fast. Both kinds of clear take the one
    // neutral, never the healthy tone.
    const hot: CodeHealthMapFile = {
      ...f("core/hot.py", 120, "core"),
      performance_opportunities: 7,
      performance_actionability: "plan_ready",
      performance_analyzed: true,
    };
    const clean: CodeHealthMapFile = {
      ...f("core/clean.py", 80, "core"),
      performance_opportunities: 0,
      performance_analyzed: true,
    };
    const unsupported: CodeHealthMapFile = {
      ...f("core/leveldb.cc", 60, "core"),
      performance_opportunities: 0,
      performance_analyzed: false,
    };
    expect(performanceFill(clean)).toBe(NEUTRAL_FILL);
    expect(performanceFill(unsupported)).toBe(NEUTRAL_FILL);
    expect(performanceFill(hot)).toBe(NODE_RAMP_STOP.alert);
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
      <CodeHealthMap files={[f("a.py", 30, "core")]} onOverlayChange={onOverlayChange} />,
    );
    // One switcher implementation, on the canvas and off it, so the two can
    // never disagree about what picking a lens means.
    fireEvent.click(getByRole("radio", { name: "Maintainability" }));
    expect(onOverlayChange).toHaveBeenCalledWith("maintainability");
  });

  it("offers only the lenses it was given", () => {
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

  it("hides a lens the rows carry no data for", () => {
    // Dead code and security colour from fields this feed does not serve; on
    // offer they would paint every node grey and read as "clean".
    const { queryByRole, getByRole } = render(
      <CodeHealthMap
        files={[f("a.py", 30, "core")]}
        onOverlayChange={vi.fn()}
        lenses={["health", "dead-code", "security"]}
      />,
    );
    expect(getByRole("radio", { name: "Code health" })).toBeInTheDocument();
    expect(queryByRole("radio", { name: "Dead code" })).not.toBeInTheDocument();
    expect(queryByRole("radio", { name: "Security" })).not.toBeInTheDocument();
  });

  it('chrome="none" renders neither the switcher nor the legend', () => {
    const { queryByText, queryByRole } = render(
      <CodeHealthMap files={[f("a.py", 30, "core")]} onOverlayChange={vi.fn()} chrome="none" />,
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
    expect(getByRole("radio", { name: "Code health" })).toBeChecked();
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
