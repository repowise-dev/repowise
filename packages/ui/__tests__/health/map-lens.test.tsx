/**
 * The lenses on the one galaxy: what the field says, what it refuses to say,
 * and what it costs to interact with.
 */
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import {
  CodeHealthMap,
  OVERLAY_SPECS,
  QUIET_FILL,
  burdenBand,
  performanceBurden,
  performanceFill,
  performanceSentence,
  type CodeHealthMapFile,
  type MapScope,
} from "../../src/health/code-health-map.js";
import { MapFieldList } from "../../src/health/map/inspector.js";
import { buildScene, groupBatches } from "../../src/health/map/paint.js";
import { deriveAreaIndex } from "../../src/health/map/areas.js";
import { layoutAreas, layoutFiles } from "../../src/health/map/area-layout.js";
import { healthRampFill } from "../../src/health/tokens.js";
import { mapFile as f, nextFrame, nodeAt, stubMapEnvironment } from "./map-test-utils.js";

/** The flat feed laid out as the map lays it out. */
function placeAll(files: CodeHealthMapFile[]) {
  const index = deriveAreaIndex(files);
  return layoutFiles(files, index, layoutAreas(index));
}

beforeAll(stubMapEnvironment);

const scope: MapScope = {
  shown: 2,
  eligible: 40,
  repository: 44,
  cap: 2,
  omitted: { files: 38, performanceFiles: 3, opportunities: 9, observations: 14 },
};

describe("burden encoding", () => {
  it("bands the count rather than scaling with it", () => {
    // Forty causes and five are the same instruction to the reader. An
    // encoding that grew with the raw count would claim a magnitude the
    // analysis has not measured.
    expect(burdenBand(0)).toBe(0);
    expect(burdenBand(1)).toBe(1);
    expect([burdenBand(2), burdenBand(4)]).toEqual([2, 2]);
    expect([burdenBand(5), burdenBand(40)]).toEqual([3, 3]);
  });

  it("leads with the best available step, not the most common one", () => {
    const row = f("a.py", 10, "core", {
      performance_opportunities: 10,
      performance_actionability: "plan_ready",
      performance_analyzed: true,
    });
    expect(performanceBurden(row).state).toBe("actionable");
    // Named in words, because the field has no channel left to say it in.
    expect(performanceSentence(row)).toContain("stored plan");
  });

  it("colours the node on the health ramp's stops, with no palette of its own", () => {
    const at = (n: number) =>
      performanceFill(f("a.py", 10, "core", {
        performance_opportunities: n,
        performance_analyzed: true,
      }));
    // The Fair, Needs work and At risk fills, so one colour means one
    // severity anywhere on this field.
    const health = (score: number) => OVERLAY_SPECS.health.fill({ ...f("s.py", 1, null), score });
    expect(at(1)).toBe(health(6));
    expect(at(3)).toBe(health(5));
    expect(at(9)).toBe(health(1));
  });

  it("never paints a file the healthy tone, whatever its state", () => {
    // The quiet tone means the code is fine on this map and no performance
    // verdict is that: a cleared file is one with no supported pattern in it,
    // not one measured to be fast.
    const fills = [0, 1, 3, 9, 40].map((n) =>
      performanceFill(f("a.py", 10, "core", { performance_opportunities: n })),
    );
    expect(fills).not.toContain(QUIET_FILL);
  });

  it("gives every file with no open cause the one neutral", () => {
    // Analyzed-clear and no-detector-for-this-language is a real distinction
    // and an undecodable one as a second shade of grey. The field says only
    // "nothing surfaced"; the words carry the rest.
    const clear = f("a.py", 10, "core", {
      performance_opportunities: 0,
      performance_analyzed: true,
    });
    const unsupported = f("c.cc", 10, "core", {
      performance_opportunities: 0,
      performance_analyzed: false,
    });
    expect(performanceFill(clear)).toBe(performanceFill(unsupported));
    expect(performanceSentence(clear)).not.toBe(performanceSentence(unsupported));
  });

  it("gives an unsupported language no clear verdict", () => {
    const row = f("a.cc", 10, "core", {
      performance_opportunities: 0,
      performance_analyzed: false,
    });
    expect(performanceBurden(row).state).toBe("unsupported");
    expect(performanceSentence(row)).not.toMatch(/nothing surfaced/i);
  });
});

describe("the health ramp", () => {
  it("paints the five canonical bands, so a dot and its score pill agree", () => {
    const fills = [9, 8.5, 7.5, 7, 6, 5.5, 5, 4, 3, 1].map(healthRampFill);
    expect(fills[0]).toBe(fills[1]);
    expect(fills[2]).toBe(fills[3]);
    expect(fills[4]).toBe(fills[5]);
    expect(fills[6]).toBe(fills[7]);
    expect(fills[8]).toBe(fills[9]);
    expect(new Set(fills).size).toBe(5);
  });

  it("uses the Good fill as the quiet tone banded lenses share", () => {
    expect(healthRampFill(7.5)).toBe(QUIET_FILL);
  });

  it("stays five colours, so a canvas draws five batches", () => {
    const fills = new Set(Array.from({ length: 901 }, (_, i) => healthRampFill(1 + i / 100)));
    expect(fills.size).toBe(5);
  });
});

describe("batches", () => {
  const files = Array.from({ length: 60 }, (_, i) =>
    f(`m${i % 3}/f${i}.py`, 20 + i, `m${i % 3}`, { score: i % 2 ? 9 : 3 }),
  );

  it("groups each host's files by fill, so a frame is one fill per colour per drawn disc", () => {
    const scene = buildScene(placeAll(files));
    const fills = scene.nodes.map((n) => OVERLAY_SPECS.health.fill(n.file));
    const batches = groupBatches(scene, fills, null, () => false, () => [0, 0, 0]);
    expect(batches).toHaveLength(scene.hosts.length * new Set(fills).size);
    expect(batches.reduce((n, b) => n + b.nodes.length, 0)).toBe(files.length);
    for (const b of batches) for (const i of b.nodes) expect(scene.hostOfNode[i]).toBe(b.host);
  });

  it("pairs the old and new fill during a lens tween", () => {
    const scene = buildScene(placeAll(files));
    const health = scene.nodes.map((n) => OVERLAY_SPECS.health.fill(n.file));
    const perf = scene.nodes.map((n) => OVERLAY_SPECS.performance.fill(n.file));
    const tween = groupBatches(scene, perf, health, () => false, (c) => (c === perf[0] ? [1, 1, 1] : [0, 0, 0]));
    expect(tween.reduce((n, b) => n + b.nodes.length, 0)).toBe(files.length);
    expect(new Set(tween.map((b) => `${b.from}|${b.to}`)).size).toBeGreaterThan(0);
  });
});

describe("scope disclosure", () => {
  it("states the drawn count against the eligible count, not the repository", () => {
    const { getByTestId } = render(
      <CodeHealthMap files={[f("a.py", 10, "core"), f("b.py", 5, "core")]} scope={scope} />,
    );
    const note = getByTestId("map-scope").textContent ?? "";
    expect(note).toContain("2 of 40");
    expect(note).not.toContain("44");
  });

  it("names the files with causes the cap pushed out", () => {
    const { getByTestId } = render(<CodeHealthMap files={[f("a.py", 10, "core")]} scope={scope} />);
    expect(getByTestId("map-scope").textContent).toContain("3 files with open causes not drawn");
  });

  it("says so when the cap left no cause behind", () => {
    const { getByTestId } = render(
      <CodeHealthMap
        files={[f("a.py", 10, "core")]}
        scope={{ ...scope, omitted: { ...scope.omitted, performanceFiles: 0 } }}
      />,
    );
    expect(getByTestId("map-scope").textContent).toContain(
      "every file with an open cause is drawn",
    );
  });

  it("offers to draw more while the field is capped, bounded by what is left", () => {
    const onDrawMore = vi.fn();
    const { getByRole } = render(
      <CodeHealthMap files={[f("a.py", 10, "core")]} scope={scope} onDrawMore={onDrawMore} />,
    );
    fireEvent.click(getByRole("button", { name: "Draw 38 more" }));
    expect(onDrawMore).toHaveBeenCalled();
  });

  it("offers no draw-more once everything is drawn", () => {
    const { queryByRole } = render(
      <CodeHealthMap
        files={[f("a.py", 10, "core")]}
        scope={{ ...scope, shown: 40 }}
        onDrawMore={vi.fn()}
      />,
    );
    expect(queryByRole("button", { name: /Draw .* more/ })).not.toBeInTheDocument();
  });

  it("names the held-out vendored and generated files and offers them", () => {
    const onIncludeHeldChange = vi.fn();
    const { getByRole } = render(
      <CodeHealthMap
        files={[f("a.py", 10, "core")]}
        scope={{ ...scope, held: 12, includeHeld: false }}
        onIncludeHeldChange={onIncludeHeldChange}
      />,
    );
    fireEvent.click(getByRole("button", { name: "Include 12 vendored and generated files" }));
    expect(onIncludeHeldChange).toHaveBeenCalledWith(true);
  });

  it("offers to find search matches the field left out, then counts them", () => {
    const onFindUndrawn = vi.fn();
    const files = [f("core/needle.py", 10, "core")];
    const { getByRole, getByTestId, rerender } = render(
      <CodeHealthMap files={files} scope={scope} search="needle" onFindUndrawn={onFindUndrawn} />,
    );
    fireEvent.click(getByRole("button", { name: "Find matches outside the field" }));
    expect(onFindUndrawn).toHaveBeenCalledWith("needle");
    rerender(
      <CodeHealthMap
        files={files}
        scope={{ ...scope, query: { text: "needle", matches: 4, shown: 1 } }}
        search="needle"
        onFindUndrawn={onFindUndrawn}
      />,
    );
    expect(getByTestId("map-scope").textContent).toContain("1 of 4 matches drawn");
  });
});

describe("search", () => {
  const files = Array.from({ length: 40 }, (_, i) => f(`core/file${i}.py`, 100 - i, "core"));

  it("marks the matches without re-laying the field", () => {
    const { getByTestId, rerender } = render(<CodeHealthMap files={files} search="" />);
    const before = nodeAt(files, "core/file7.py");
    rerender(<CodeHealthMap files={files} search="file7" />);
    expect(getByTestId("map-field").getAttribute("data-matches")).toBe("1");
    // The layout is a function of the rows alone; the query never reaches it.
    expect(nodeAt(files, "core/file7.py")).toEqual(before);
  });

  it("counts every match on the drawn field", () => {
    const many = Array.from({ length: 300 }, (_, i) => f(`core/hit${i}.py`, 50, "core"));
    const { getByTestId } = render(<CodeHealthMap files={many} search="hit" />);
    expect(getByTestId("map-field").getAttribute("data-matches")).toBe("300");
  });
});

describe("what the field costs to draw", () => {
  it("places every drawable file exactly once", () => {
    const files = Array.from({ length: 300 }, (_, i) =>
      f(`core/f${i}.py`, 200 - (i % 150), i % 2 ? "core" : "ui"),
    );
    const placed = placeAll(files).nodes.map((n) => n.file.file_path);
    expect(placed).toHaveLength(300);
    expect(new Set(placed).size).toBe(300);
  });

  it("renders no DOM node per file and no SVG filter", () => {
    const files = Array.from({ length: 300 }, (_, i) => f(`core/f${i}.py`, 50 + i, "core"));
    const { container } = render(<CodeHealthMap files={files} overlay="performance" />);
    expect(container.querySelectorAll("circle")).toHaveLength(0);
    expect(container.querySelectorAll("filter")).toHaveLength(0);
    expect(container.querySelectorAll("title")).toHaveLength(0);
  });
});

describe("the hover card", () => {
  const files = [f("core/deep/nested/alpha.py", 120, "core"), f("ui/beta.py", 60, "ui")];

  it("opens at the pointer rather than in a corner of the canvas", async () => {
    // A card pinned to one corner makes every identification a round trip
    // across the field, and the pointer has usually left the node by the time
    // the eye gets back.
    const { getByTestId } = render(<CodeHealthMap files={files} />);
    await nextFrame();
    fireEvent.mouseMove(getByTestId("map-canvas"), nodeAt(files, "core/deep/nested/alpha.py"));
    const card = getByTestId("map-hover-card");
    expect(card.textContent).toContain("alpha.py");
    // The directory is present but subordinate: the filename is what is being
    // pointed at, and it is the last thing a truncated path would show.
    expect(card.textContent).toContain("core/deep/nested/");
    expect(card.getAttribute("style")).toMatch(/left:/);
  });

  it("closes when the pointer leaves the node, and tells the host", async () => {
    const onHoverFile = vi.fn();
    const { getByTestId, queryByTestId } = render(
      <CodeHealthMap files={files} onHoverFile={onHoverFile} />,
    );
    const canvas = getByTestId("map-canvas");
    await nextFrame();
    fireEvent.mouseMove(canvas, nodeAt(files, "ui/beta.py"));
    expect(queryByTestId("map-hover-card")).toBeInTheDocument();
    fireEvent.mouseMove(canvas, { clientX: 2, clientY: 2 });
    expect(queryByTestId("map-hover-card")).not.toBeInTheDocument();
    expect(onHoverFile).toHaveBeenLastCalledWith(null);
  });
});

describe("keyboard", () => {
  const files = [f("core/a.py", 120, "core"), f("core/b.py", 60, "core"), f("ui/c.py", 40, "ui")];

  it("reaches every area and file through one tab stop", () => {
    const onSelectFile = vi.fn();
    const onAreaChange = vi.fn();
    const { getByRole, container, getByTestId, rerender } = render(
      <CodeHealthMap files={files} onSelectFile={onSelectFile} onAreaChange={onAreaChange} area="" />,
    );
    // One focusable region for the whole field. Thousands of tab stops is not
    // navigation, and a canvas with none is not reachable.
    expect(container.querySelectorAll('[tabindex="0"]')).toHaveLength(1);
    const field = getByRole("group", { name: /Code health map/ });
    // The cursor shows once the keys are used: right to ui, left back to core.
    fireEvent.keyDown(field, { key: "ArrowRight" });
    fireEvent.keyDown(field, { key: "ArrowLeft" });
    expect(getByTestId("map-field").getAttribute("data-keyboard-cursor")).toBe("core"); // biggest first
    fireEvent.keyDown(field, { key: "Enter" });
    expect(onAreaChange).toHaveBeenCalledWith("core");

    rerender(<CodeHealthMap files={files} onSelectFile={onSelectFile} onAreaChange={onAreaChange} area="core" />);
    fireEvent.keyDown(field, { key: "ArrowRight" }); // second file in core
    expect(getByTestId("map-field").getAttribute("data-keyboard-cursor")).toBe("core/b.py");
    fireEvent.keyDown(field, { key: "Enter" });
    expect(onSelectFile).toHaveBeenCalledWith("core/b.py");
  });

  it("announces where the cursor is", () => {
    const { getByRole, container } = render(<CodeHealthMap files={files} area="core" onAreaChange={vi.fn()} />);
    const field = getByRole("group", { name: /Code health map/ });
    fireEvent.keyDown(field, { key: "ArrowRight" });
    fireEvent.keyDown(field, { key: "ArrowLeft" });
    const live = container.querySelector('[aria-live="polite"]');
    expect(live?.textContent).toContain("core/a.py");
  });

  it("describes the field in words for a screen reader", () => {
    const { container } = render(<CodeHealthMap files={files} />);
    expect(container.textContent).toMatch(/3 files in 2 folders/);
  });
});

describe("the navigable list beside the field", () => {
  const files = [
    f("core/a.py", 120, "core", {
      performance_opportunities: 5,
      performance_observations: 8,
      performance_actionability: "advisory",
      performance_rank: 2,
      performance_analyzed: true,
    }),
    f("core/b.py", 60, "core", {
      performance_opportunities: 1,
      performance_actionability: "plan_ready",
      performance_rank: 0,
      performance_analyzed: true,
    }),
    f("ui/c.py", 40, "ui", { performance_opportunities: 0, performance_analyzed: true }),
  ];

  it("ranks by the queue's own order and opens the same selection a click does", () => {
    const onSelectFile = vi.fn();
    const { getAllByRole } = render(
      <MapFieldList files={files} overlay="performance" onSelectFile={onSelectFile} />,
    );
    const rows = getAllByRole("button");
    expect(rows[0]!.textContent).toContain("core/b.py"); // rank 0 leads
    fireEvent.click(rows[0]!);
    expect(onSelectFile).toHaveBeenCalledWith("core/b.py");
  });

  it("lists only files with a cause under the performance lens, and says how many", () => {
    const { getAllByRole, getByText } = render(
      <MapFieldList files={files} overlay="performance" onSelectFile={vi.fn()} scope={scope} />,
    );
    expect(getAllByRole("button")).toHaveLength(2);
    expect(getByText(/3 more carry causes outside the drawn field/)).toBeInTheDocument();
  });

  it("says plainly when the drawn field carries none", () => {
    const { getByText } = render(
      <MapFieldList
        files={[f("ui/c.py", 40, "ui", { performance_opportunities: 0 })]}
        overlay="performance"
        onSelectFile={vi.fn()}
      />,
    );
    expect(getByText(/No file in the drawn field carries an open performance cause/)).toBeInTheDocument();
  });

  it("ranks worst-score-first under a score lens", () => {
    const scored = [f("a.py", 10, "core"), { ...f("b.py", 10, "core"), score: 2 }];
    const { getAllByRole } = render(
      <MapFieldList files={scored} overlay="health" onSelectFile={vi.fn()} />,
    );
    expect(getAllByRole("button")[0]!.textContent).toContain("b.py");
  });
});
