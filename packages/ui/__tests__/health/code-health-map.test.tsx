import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import {
  CodeHealthMap,
  MapLegend,
  NEUTRAL_FILL,
  MapLensSwitcher,
  performanceBurden,
  performanceFill,
  performanceSentence,
  type CodeHealthMapFile,
} from "../../src/health/code-health-map.js";
import { NODE_RAMP_STOP } from "../../src/health/tokens.js";
import { nextFrame, nodeAt, stubMapEnvironment } from "./map-test-utils.js";

function f(
  file_path: string,
  nloc: number,
  module: string | null,
  score = 7,
): CodeHealthMapFile {
  return { file_path, nloc, score, module, line_coverage_pct: null, has_test_file: false };
}

beforeAll(stubMapEnvironment);

describe("CodeHealthMap", () => {
  it("renders the empty state when there are no files", () => {
    const { getByText } = render(<CodeHealthMap files={[]} />);
    expect(getByText(/No files to map yet/i)).toBeInTheDocument();
  });

  it("opens a file when its node is clicked", async () => {
    const onSelectFile = vi.fn();
    const files = [
      f("core/a.py", 120, "core", 3),
      f("core/b.py", 60, "core", 8),
      f("ui/c.py", 40, "ui", 6),
    ];
    const { getByTestId } = render(<CodeHealthMap files={files} onSelectFile={onSelectFile} />);
    await nextFrame();
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

  it("opens an area from its name, and Escape returns to the repository", async () => {
    const files = [f("core/a.py", 120, "core"), f("ui/c.py", 40, "ui")];
    const { getByRole, getByTestId } = render(<CodeHealthMap files={files} />);
    await nextFrame();
    fireEvent.click(getByTestId("map-field").parentElement!.querySelector('[data-area="core"]')!);
    expect(getByTestId("map-field").getAttribute("data-area")).toBe("core");
    expect(getByRole("navigation", { name: "Breadcrumb" }).textContent).toContain("core");
    fireEvent.keyDown(getByRole("group", { name: /Code health map/ }), { key: "Escape" });
    expect(getByTestId("map-field").getAttribute("data-area")).toBe("");
  });

  it("Backspace climbs one level and the breadcrumb climbs to any", () => {
    const onAreaChange = vi.fn();
    const files = [f("pkg/core/a.py", 120, "core"), f("pkg/core/b.py", 20, "core"), f("pkg/ui/c.py", 40, "ui")];
    const { getByRole } = render(<CodeHealthMap files={files} area="pkg/core" onAreaChange={onAreaChange} />);
    fireEvent.keyDown(getByRole("group", { name: /Code health map/ }), { key: "Backspace" });
    expect(onAreaChange).toHaveBeenLastCalledWith("pkg");
    fireEvent.click(getByRole("button", { name: "Repository" }));
    expect(onAreaChange).toHaveBeenLastCalledWith("");
  });

  it("leaves an Escape aimed at something else alone", () => {
    const onAreaChange = vi.fn();
    const files = [f("core/a.py", 120, "core"), f("ui/c.py", 40, "ui")];
    render(<CodeHealthMap files={files} area="core" onAreaChange={onAreaChange} />);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onAreaChange).not.toHaveBeenCalled();
  });

  it("keeps an Escape it used from reaching anything behind it", () => {
    const files = [f("core/a.py", 120, "core"), f("ui/c.py", 40, "ui")];
    const { getByRole, getByTestId } = render(<CodeHealthMap files={files} area="core" onAreaChange={vi.fn()} />);
    const behind = vi.fn();
    document.addEventListener("keydown", behind, true);
    fireEvent.keyDown(getByRole("group", { name: /Code health map/ }), { key: "Escape" });
    expect(behind).not.toHaveBeenCalled();
    document.removeEventListener("keydown", behind, true);
    expect(getByTestId("map-field").getAttribute("data-area")).toBe("core");
  });

  it("offers zoom and fit controls with names", () => {
    const { getByRole } = render(<CodeHealthMap files={[f("a.py", 30, "core")]} />);
    for (const name of ["Zoom in", "Zoom out", "Frame the selected area"]) {
      expect(getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("shows the on-canvas health legend on the five canonical bands", () => {
    const { getByText } = render(<CodeHealthMap files={[f("a.py", 30, "core")]} />);
    expect(getByText("Code health")).toBeInTheDocument();
    expect(getByText(/disc = folder/i)).toBeInTheDocument();
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

  it("puts the lenses past the primary ones behind More, and keeps the active one visible", () => {
    const onOverlayChange = vi.fn();
    const lenses = ["health", "hotspot", "owner", "performance", "coverage"] as const;
    const primary = ["health", "hotspot", "owner"] as const;
    const { getByRole, queryByRole, rerender } = render(
      <MapLensSwitcher overlay="health" onOverlayChange={onOverlayChange} lenses={[...lenses]} primary={[...primary]} />,
    );
    expect(queryByRole("radio", { name: "Performance" })).not.toBeInTheDocument();
    fireEvent.click(getByRole("button", { name: "More" }));
    fireEvent.click(getByRole("menuitemradio", { name: "Performance" }));
    expect(onOverlayChange).toHaveBeenCalledWith("performance");
    rerender(
      <MapLensSwitcher overlay="performance" onOverlayChange={onOverlayChange} lenses={[...lenses]} primary={[...primary]} />,
    );
    expect(getByRole("radio", { name: "Performance" })).toBeChecked();
  });

  it("MapLegend renders the active lens's bands and caption", () => {
    const { getByText } = render(<MapLegend overlay="performance" />);
    expect(getByText("5 or more")).toBeInTheDocument();
    expect(getByText("Nothing surfaced or not analysed")).toBeInTheDocument();
    expect(getByText("No issues found")).toBeInTheDocument();
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
