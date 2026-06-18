import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import {
  CodeHealthMap,
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
    // Nodes carry a <title> with the path; pick one and click its circle.
    const titles = Array.from(container.querySelectorAll("title"));
    const target = titles.find((t) => t.textContent?.startsWith("core/a.py"));
    expect(target).toBeTruthy();
    fireEvent.click(target!.parentElement!);
    expect(onSelectFile).toHaveBeenCalledWith("core/a.py");
  });

  it("zooms into a galaxy and Escape returns to the overview", () => {
    const files = [f("core/a.py", 120, "core"), f("ui/c.py", 40, "ui")];
    const { getByText, queryByText, container } = render(<CodeHealthMap files={files} />);
    // Click a galaxy nebula (a blurred blob) to focus it.
    const blob = container.querySelector('circle[filter="url(#ch-nebula)"]');
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

  it("fires onOverlayChange when a lens-switch button is clicked", () => {
    const onOverlayChange = vi.fn();
    const { getByRole } = render(
      <CodeHealthMap
        files={[f("a.py", 30, "core")]}
        onOverlayChange={onOverlayChange}
      />,
    );
    // The lens switcher renders one toggle button per lens; click "Churn".
    fireEvent.click(getByRole("button", { name: "Churn" }));
    expect(onOverlayChange).toHaveBeenCalledWith("churn");
  });
});
