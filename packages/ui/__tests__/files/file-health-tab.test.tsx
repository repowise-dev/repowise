import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FileHealthTab } from "../../src/files/file-health-tab.js";
import type {
  FileDetailHealth,
  FileHealthFinding,
  FunctionBlameRow,
} from "@repowise-dev/types/files";

function makeFinding(i: number): FileHealthFinding {
  return {
    id: `f${i}`,
    file_path: "src/app.ts",
    biomarker_type: "long_function",
    severity: "warning",
    function_name: `fn_${i}`,
    line_start: 10 + i,
    line_end: 40 + i,
    health_impact: 1.25,
    reason: `Finding reason ${i}`,
    details: {},
    status: "open",
  };
}

function makeBlame(i: number): FunctionBlameRow {
  return {
    symbol_id: `sym${i}`,
    function_name: `func_${i}`,
    start_line: 100 + i,
    end_line: 120 + i,
    line_count: 20,
    mod_count: 5 + i,
    recent_mod_count: i,
    median_author_time: i % 2 === 0 ? Math.floor(Date.now() / 1000) - 86400 * 3 : null,
    owner_name: `Owner ${i}`,
    owner_email: `owner${i}@example.com`,
    owner_line_pct: 0.5,
  };
}

function makeHealth(findings: FileHealthFinding[]): FileDetailHealth {
  return {
    metric: null,
    breakdown: null,
    findings,
    trend: null,
    signals: null,
  };
}

describe("FileHealthTab", () => {
  it("renders every finding card and blame row for a small dataset", () => {
    const findings = Array.from({ length: 5 }, (_, i) => makeFinding(i));
    const blame = Array.from({ length: 6 }, (_, i) => makeBlame(i));
    render(<FileHealthTab health={makeHealth(findings)} functionBlame={blame} />);

    for (let i = 0; i < 5; i++) {
      expect(screen.getByText(`Finding reason ${i}`)).toBeTruthy();
    }
    for (let i = 0; i < 6; i++) {
      expect(screen.getByText(`func_${i}`)).toBeTruthy();
      expect(screen.getByText(`Owner ${i}`)).toBeTruthy();
    }
  });

  it("renders the median-age cell from hoisted compute and the blame header", () => {
    const blame = [makeBlame(0)]; // even index => has author time => age in days
    // One finding so the component renders past the no-data empty state and
    // shows the blame table whose median-age cell comes from the hoisted compute.
    render(<FileHealthTab health={makeHealth([makeFinding(0)])} functionBlame={blame} />);

    expect(screen.getByText("Function")).toBeTruthy();
    expect(screen.getByText("Median age")).toBeTruthy();
    expect(screen.getByText("3d")).toBeTruthy(); // 3 days old
  });

  it("shows finding status buttons when a change handler is provided", () => {
    const findings = [makeFinding(0)];
    render(
      <FileHealthTab
        health={makeHealth(findings)}
        functionBlame={[]}
        onFindingStatusChange={() => {}}
      />,
    );
    expect(screen.getByText("Acknowledged")).toBeTruthy();
    expect(screen.getByText("Resolved")).toBeTruthy();
  });

  it("renders an empty state when there is no metric and no findings", () => {
    render(<FileHealthTab health={makeHealth([])} functionBlame={[]} />);
    expect(screen.getByText("No health data")).toBeTruthy();
  });
});
