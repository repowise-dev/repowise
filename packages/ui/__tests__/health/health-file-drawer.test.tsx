import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import {
  HealthFileDrawer,
  type HealthDrawerFinding,
  type HealthDrawerMetric,
} from "../../src/health/health-file-drawer.js";

function metric(partial: Partial<HealthDrawerMetric> = {}): HealthDrawerMetric {
  return {
    file_path: "packages/cli/doctor_cmd.py",
    score: 1.0,
    max_ccn: 40,
    max_nesting: 6,
    nloc: 800,
    module: "cli",
    has_test_file: false,
    ...partial,
  };
}

let seq = 0;
function finding(partial: Partial<HealthDrawerFinding> = {}): HealthDrawerFinding {
  seq += 1;
  return {
    id: `f${seq}`,
    biomarker_type: "brain_method",
    severity: "high",
    function_name: "_run_repo_checks",
    line_start: 120,
    line_end: 487,
    health_impact: 1.5,
    reason: "Oversized, deeply-nested function.",
    ...partial,
  };
}

describe("HealthFileDrawer finding grouping", () => {
  it("collapses many markers on one function into a single group header", () => {
    const findings: HealthDrawerFinding[] = [
      finding({ biomarker_type: "brain_method", health_impact: 3.0 }),
      finding({ biomarker_type: "long_method", health_impact: 2.0 }),
      finding({ biomarker_type: "deep_nesting", health_impact: 1.0 }),
    ];
    render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={findings} />,
    );

    // One group header names the function + its worst marker, and sums impact.
    const header = screen.getByRole("button", { name: /_run_repo_checks/ });
    expect(within(header).getByText(/3 markers/)).toBeInTheDocument();
    expect(within(header).getByText(/−6\.00/)).toBeInTheDocument();
  });

  it("keeps file-level markers (no function) in their own group", () => {
    const findings: HealthDrawerFinding[] = [
      finding({ function_name: "_run_repo_checks", health_impact: 5.0 }),
      finding({
        biomarker_type: "change_entropy",
        function_name: null,
        line_start: null,
        health_impact: 2.0,
        reason: "File changes touch many unrelated concerns.",
      }),
    ];
    render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={findings} />,
    );
    expect(screen.getByText("File-level signals")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /_run_repo_checks/ })).toBeInTheDocument();
  });
});
