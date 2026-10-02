/**
 * A file in a language health has no dialect for arrives with `score: null`.
 * It is drawn grey, like a file no performance detector covers, and is never
 * printed as a score. A field with no scored file says why in one line.
 */
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import type { HealthOverviewSummary } from "@repowise-dev/types/health";
import {
  CodeHealthMap,
  HEALTH_UNSUPPORTED_LABEL,
  HEALTH_UNSUPPORTED_NOTICE,
  NEUTRAL_FILL,
  OVERLAY_SPECS,
  PERFORMANCE_STATE_LABEL,
  noFileScored,
  type CodeHealthMapFile,
} from "../../src/health/code-health-map.js";
import { MapFieldList, MapInspector } from "../../src/health/map/inspector.js";
import { CodeHealthLede } from "../../src/health/code-health-lede.js";

function f(path: string, score: number | null, nloc = 100): CodeHealthMapFile {
  return {
    file_path: path,
    score,
    nloc,
    module: "src",
    line_coverage_pct: null,
    has_test_file: false,
    maintainability_score: score,
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

describe("an unscored file on the health lens", () => {
  it("is grey, not the green a perfect score would draw", () => {
    expect(OVERLAY_SPECS.health.fill(f("src/Big.php", null))).toBe(NEUTRAL_FILL);
    expect(OVERLAY_SPECS.health.fill(f("src/ok.py", 10))).not.toBe(NEUTRAL_FILL);
  });

  it("is named in the key with the performance lens's own words", () => {
    expect(HEALTH_UNSUPPORTED_LABEL).toBe(PERFORMANCE_STATE_LABEL.unsupported);
    expect(OVERLAY_SPECS.health.legend.at(-1)).toEqual({
      fill: NEUTRAL_FILL,
      label: HEALTH_UNSUPPORTED_LABEL,
    });
  });

  it("ranks last and prints no score in the list", () => {
    const { container } = render(
      <MapFieldList
        files={[f("src/Big.php", null), f("src/bad.py", 3), f("src/ok.py", 9)]}
        overlay="health"
        onSelectFile={() => {}}
      />,
    );
    const names = Array.from(container.querySelectorAll("li")).map((li) => li.textContent ?? "");
    expect(names[0]).toContain("bad.py");
    expect(names[2]).toContain("Big.php");
    expect(names[2]).toContain("—");
  });

  it("is described, not scored, in the inspector", () => {
    render(
      <MapInspector
        file={f("src/Big.php", null)}
        overlay="health"
        onOpen={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(HEALTH_UNSUPPORTED_LABEL)).toBeTruthy();
    expect(screen.queryByText("10.0")).toBeNull();
  });
});

describe("a field with no scored file", () => {
  it("is recognised only when every file is unscored", () => {
    expect(noFileScored([f("a.php", null), f("b.php", null)])).toBe(true);
    expect(noFileScored([f("a.php", null), f("b.py", 8)])).toBe(false);
    expect(noFileScored([])).toBe(false);
  });

  it("says so in one line over the map", () => {
    render(<CodeHealthMap files={[f("a.php", null), f("b.php", null, 40)]} />);
    expect(screen.getByTestId("map-unanalysed").textContent).toBe(HEALTH_UNSUPPORTED_NOTICE);
  });

  it("says nothing when some file is scored", () => {
    render(<CodeHealthMap files={[f("a.php", null), f("b.py", 7, 40)]} />);
    expect(screen.queryByTestId("map-unanalysed")).toBeNull();
  });
});

describe("the code health lede", () => {
  const base = {
    file_count: 0,
    open_findings: 0,
    worst_performer_path: null,
    worst_performer_score: null,
  };

  it("leads with 'Not analysed' when no file is scored", () => {
    render(
      <CodeHealthLede
        summary={{ ...base, average_health: null, unanalysed_file_count: 2992 } as HealthOverviewSummary}
      />,
    );
    expect(screen.getByText("Not analysed")).toBeTruthy();
    expect(screen.getByText(HEALTH_UNSUPPORTED_NOTICE)).toBeTruthy();
    expect(screen.queryByText("out of 10")).toBeNull();
  });

  it("names the files it left out of a scored figure", () => {
    const { container } = render(
      <CodeHealthLede
        summary={
          { ...base, file_count: 253, average_health: 9.1, unanalysed_file_count: 1650 } as HealthOverviewSummary
        }
      />,
    );
    expect(container.textContent).toContain("1,650 more files are in a");
  });
});
