/**
 * The performance lens on the one galaxy: what the field says, what it refuses
 * to say, and what it costs to interact with.
 */
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import {
  CodeHealthMap,
  OVERLAY_SPECS,
  SEARCH_MARK_CAP,
  burdenBand,
  performanceBurden,
  performanceFill,
  performanceSentence,
  type CodeHealthMapFile,
  type MapScope,
} from "../../src/health/code-health-map.js";
import { MapFieldList, MapInspector } from "../../src/health/map/inspector.js";

function f(
  file_path: string,
  nloc: number,
  module: string | null,
  extra: Partial<CodeHealthMapFile> = {},
): CodeHealthMapFile {
  return {
    file_path,
    nloc,
    score: 7,
    module,
    line_coverage_pct: null,
    has_test_file: false,
    ...extra,
  };
}

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

  it("colours the node on the health ramp, with no palette of its own", () => {
    const at = (n: number) =>
      performanceFill(f("a.py", 10, "core", {
        performance_opportunities: n,
        performance_analyzed: true,
      }));
    // The same tokens the health lens paints with. An earlier cut mixed its
    // own tones toward the page, putting colours on this field that exist
    // nowhere else in the product and going muddy against a dark root.
    const health = (score: number) => OVERLAY_SPECS.health.fill({ ...f("s.py", 1, null), score });
    expect(at(1)).toBe(health(7)); // the ramp's fair step
    expect(at(3)).toBe(health(5)); // poor
    expect(at(9)).toBe(health(1)); // critical
  });

  it("never paints a file the healthy green, whatever its state", () => {
    // Green means healthy on this map and no performance verdict is that: a
    // cleared file is one with no supported pattern in it, not one measured to
    // be fast. So the lens uses three of the ramp's four bands.
    const green = OVERLAY_SPECS.health.fill({ ...f("s.py", 1, null), score: 9 });
    const fills = [0, 1, 3, 9, 40].map((n) =>
      performanceFill(f("a.py", 10, "core", { performance_opportunities: n })),
    );
    expect(fills).not.toContain(green);
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

  it("spends opacity on focus alone, never on data", () => {
    const files = [
      f("core/a.py", 100, "core", { performance_opportunities: 0, performance_analyzed: true }),
      f("core/b.py", 90, "core", { performance_opportunities: 6, performance_analyzed: true }),
    ];
    const { container } = render(<CodeHealthMap files={files} overlay="performance" />);
    // A lens that also dimmed by burden left a reader unable to tell a quiet
    // file from one sitting in an unfocused galaxy, and dissolved the field
    // into a wash by dropping the separating stroke under it.
    for (const node of container.querySelectorAll("circle[data-path]")) {
      expect(node.getAttribute("fill-opacity")).toBe("0.9");
      expect(Number(node.getAttribute("stroke-width"))).toBeGreaterThan(0);
    }
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
    const { getByTestId } = render(
      <CodeHealthMap files={[f("a.py", 10, "core")]} scope={scope} />,
    );
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
});

describe("search", () => {
  const files = Array.from({ length: 40 }, (_, i) =>
    f(`core/file${i}.py`, 100 - i, "core"),
  );

  it("marks the matches instead of touching the field", () => {
    const { container, rerender } = render(<CodeHealthMap files={files} search="" />);
    const before = container.querySelector('circle[data-path="core/file7.py"]');
    rerender(<CodeHealthMap files={files} search="file7" />);
    const after = container.querySelector('circle[data-path="core/file7.py"]');
    // The same DOM node, with the same attributes: the base layer was not
    // reconciled, so typing costs the marks and nothing else.
    expect(after).toBe(before);
    expect(after!.getAttribute("fill-opacity")).toBe("0.9");
    expect(container.querySelectorAll("circle[data-match]").length).toBeGreaterThan(0);
  });

  it("does not re-lay the field when the query changes", () => {
    const { container, rerender } = render(<CodeHealthMap files={files} search="" />);
    const at = (p: string) => {
      const n = container.querySelector(`circle[data-path="${p}"]`)!;
      return `${n.getAttribute("cx")},${n.getAttribute("cy")},${n.getAttribute("r")}`;
    };
    const before = at("core/file3.py");
    rerender(<CodeHealthMap files={files} search="fi" />);
    rerender(<CodeHealthMap files={files} search="file" />);
    expect(at("core/file3.py")).toBe(before);
  });

  it("bounds how many matches it draws", () => {
    const many = Array.from({ length: SEARCH_MARK_CAP + 30 }, (_, i) =>
      f(`core/hit${i}.py`, 50, "core"),
    );
    const { container } = render(<CodeHealthMap files={many} search="hit" />);
    expect(container.querySelectorAll("circle[data-match]").length).toBe(SEARCH_MARK_CAP);
  });

  it("marks the paths a deep link asked for, with no query at all", () => {
    const { container } = render(
      <CodeHealthMap files={files} highlightPaths={["core/file2.py"]} />,
    );
    expect(container.querySelector('circle[data-match="core/file2.py"]')).toBeTruthy();
  });
});

describe("what the field costs to draw", () => {
  const files = Array.from({ length: 300 }, (_, i) =>
    f(`core/f${i}.py`, 200 - (i % 150), i % 2 ? "core" : "ui", {
      performance_opportunities: i % 5 === 0 ? (i % 9) + 1 : 0,
      performance_actionability: "advisory",
      performance_analyzed: true,
    }),
  );

  it("draws one circle per file and no second mark on top of it", () => {
    // A rare per-file annotation was tried as a ring outside the node and as a
    // core inside it. Neither read as belonging to one file on a body this
    // dense, so the lens spends one channel and the words carry the rest.
    const planned = files.map((row, i) =>
      i % 7 === 0 ? { ...row, performance_actionability: "plan_ready" as const } : row,
    );
    const { container } = render(<CodeHealthMap files={planned} overlay="performance" />);
    const nodes = container.querySelectorAll("circle[data-path]");
    expect(nodes).toHaveLength(300);
    // Nothing is drawn concentric with a node but smaller or larger than it,
    // which is the shape both rejected marks had.
    const centres = new Set([...nodes].map((n) => `${n.getAttribute("cx")},${n.getAttribute("cy")}`));
    const overlaid = [...container.querySelectorAll("circle:not([data-path])")].filter((c) =>
      centres.has(`${c.getAttribute("cx")},${c.getAttribute("cy")}`),
    );
    expect(overlaid).toHaveLength(0);
  });

  it("keeps the safeguards that make thousands of nodes affordable", () => {
    const { container } = render(<CodeHealthMap files={files} overlay="performance" />);
    // A native tooltip per node, an offscreen raster pass, and a per-frame
    // stroke recomputation. Each was measured and removed; none may return.
    expect(container.querySelectorAll("title")).toHaveLength(0);
    expect(container.querySelectorAll("filter")).toHaveLength(0);
    expect(container.querySelectorAll("circle[data-path][vector-effect]")).toHaveLength(0);
  });
});

describe("the hover card", () => {
  const files = [f("core/deep/nested/alpha.py", 120, "core"), f("ui/beta.py", 60, "ui")];

  it("opens at the pointer rather than in a corner of the canvas", () => {
    // A card pinned to one corner makes every identification a round trip
    // across the field, and the pointer has usually left the node by the time
    // the eye gets back.
    const { container, getByTestId } = render(<CodeHealthMap files={files} />);
    const node = container.querySelector('circle[data-path="core/deep/nested/alpha.py"]')!;
    fireEvent.mouseEnter(node, { clientX: 120, clientY: 90 });
    const card = getByTestId("map-hover-card");
    expect(card.textContent).toContain("alpha.py");
    // The directory is present but subordinate: the filename is what is being
    // pointed at, and it is the last thing a truncated path would show.
    expect(card.textContent).toContain("core/deep/nested/");
    expect(card.getAttribute("style")).toMatch(/left:/);
  });

  it("closes when the pointer leaves the node", () => {
    const { container, queryByTestId } = render(<CodeHealthMap files={files} />);
    const node = container.querySelector('circle[data-path="ui/beta.py"]')!;
    fireEvent.mouseEnter(node, { clientX: 10, clientY: 10 });
    expect(queryByTestId("map-hover-card")).toBeInTheDocument();
    fireEvent.mouseLeave(node);
    expect(queryByTestId("map-hover-card")).not.toBeInTheDocument();
  });
});

describe("keyboard", () => {
  const files = [
    f("core/a.py", 120, "core"),
    f("core/b.py", 60, "core"),
    f("ui/c.py", 40, "ui"),
  ];

  it("reaches every file through one tab stop", () => {
    const onSelectFile = vi.fn();
    const { getByRole, container } = render(
      <CodeHealthMap files={files} onSelectFile={onSelectFile} />,
    );
    // One focusable region for the whole field. Thousands of tab stops is not
    // navigation, and a canvas with none is not reachable.
    expect(container.querySelectorAll('[tabindex="0"]')).toHaveLength(1);
    const field = getByRole("group", { name: /Code health map/ });

    fireEvent.keyDown(field, { key: "Enter" }); // into the first module
    fireEvent.keyDown(field, { key: "ArrowRight" }); // second file in it
    expect(container.querySelector("circle[data-keyboard-cursor]")).toBeTruthy();
    fireEvent.keyDown(field, { key: "Enter" });
    expect(onSelectFile).toHaveBeenCalledWith("core/b.py");
  });

  it("announces where the cursor is", () => {
    const { getByRole, container } = render(<CodeHealthMap files={files} />);
    const field = getByRole("group", { name: /Code health map/ });
    fireEvent.keyDown(field, { key: "Enter" });
    const live = container.querySelector('[aria-live="polite"]');
    expect(live?.textContent).toContain("core/a.py");
  });

  it("Escape climbs back out to the modules", () => {
    const { getByRole, container, queryByText } = render(<CodeHealthMap files={files} />);
    const field = getByRole("group", { name: /Code health map/ });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(queryByText("← Overview")).toBeInTheDocument();
    fireEvent.keyDown(field, { key: "Escape" });
    expect(container.querySelector("circle[data-keyboard-cursor]")).toBeNull();
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

describe("the inspector", () => {
  const row = f("core/a.py", 120, "core", {
    performance_opportunities: 5,
    performance_observations: 8,
    performance_actionability: "advisory",
    performance_analyzed: true,
  });

  it("describes the selection in the active lens's terms", () => {
    const { getByText } = render(
      <MapInspector file={row} overlay="performance" onOpen={vi.fn()} onClose={vi.fn()} />,
    );
    expect(getByText("Open opportunities")).toBeInTheDocument();
    expect(getByText("Observations behind them")).toBeInTheDocument();
    expect(getByText("Advisory")).toBeInTheDocument();
  });

  it("leads with the burden, not the defect score, under its own lens", () => {
    // The defect badge led here, so a file with a healthy score and a heavy
    // ring beside it opened an inspector that said the opposite of the mark.
    const { container, getByText } = render(
      <MapInspector file={row} overlay="performance" onOpen={vi.fn()} onClose={vi.fn()} />,
    );
    expect(getByText("Open opportunities")).toBeInTheDocument();
    expect(getByText(/defect risk 7\.0/)).toBeInTheDocument();
    // The score badge, which is the defect ramp, is not the leading mark.
    const leading = container.querySelector("section > div")?.firstElementChild;
    expect(leading?.textContent).toBe("");
  });

  it("keeps the defect score leading under a score lens", () => {
    const { getByText } = render(
      <MapInspector file={row} overlay="health" onOpen={vi.fn()} onClose={vi.fn()} />,
    );
    expect(getByText("7.0")).toBeInTheDocument();
  });

  it("says nothing about performance under another lens", () => {
    const { queryByText } = render(
      <MapInspector file={row} overlay="health" onOpen={vi.fn()} onClose={vi.fn()} />,
    );
    expect(queryByText("Open opportunities")).not.toBeInTheDocument();
  });

  it("never calls an analyzed-clear file fast", () => {
    const clear = f("core/clean.py", 10, "core", {
      performance_opportunities: 0,
      performance_analyzed: true,
    });
    const { getByText } = render(
      <MapInspector file={clear} overlay="performance" onOpen={vi.fn()} onClose={vi.fn()} />,
    );
    expect(getByText("Analyzed, nothing surfaced")).toBeInTheDocument();
  });
});
