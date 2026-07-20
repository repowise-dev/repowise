import { describe, it, expect } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
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

describe("HealthFileDrawer metric cards", () => {
  it("says 'not measured' instead of 0 when structural counters are absent", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ max_ccn: null, max_nesting: null, nloc: null })}
      />,
    );
    expect(screen.getAllByText("not measured")).toHaveLength(3);
  });

  it("still renders real zero values as numbers", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ max_ccn: 0, max_nesting: 0, nloc: 12 })}
      />,
    );
    expect(screen.queryByText("not measured")).not.toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });
});

describe("HealthFileDrawer bug history", () => {
  const signals = {
    prior_defect_count: 8,
    change_entropy_pct: null,
    lines_added_90d: null,
    lines_deleted_90d: null,
    commit_count_90d: null,
    age_days: null,
    primary_owner_name: null,
    primary_owner_commit_pct: null,
    recent_owner_name: null,
    recent_owner_commit_pct: null,
    in_degree: null,
    out_degree: null,
    bug_magnet: true,
    last_fix_at: new Date(Date.now() - 3 * 86400_000).toISOString(),
    fix_symbol_counts: { "pipeline.py::run_update": 4, "pipeline.py::persist": 2 },
  };

  it("hides per-symbol counts behind a disclosure that names the last fix", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={[]}
        signals={signals}
      />,
    );

    const toggle = screen.getByRole("button", { name: /Bug history/ });
    // Recency rides on the toggle itself: the counts are collapsed, the "is
    // this still happening?" answer is not.
    expect(toggle).toHaveTextContent(/last fix 3d ago/);
    // Collapsed by default, because "where do the bugs cluster" is not a question
    // every reader of the drawer has.
    expect(screen.queryByText("run_update")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByText("run_update")).toBeInTheDocument();
    expect(screen.getByText("4 fixes")).toBeInTheDocument();
    expect(screen.getByText("2 fixes")).toBeInTheDocument();
    // The line-range mapping is approximate and says so, rather than letting
    // the counts read as exact.
    expect(screen.getByText(/lines move/)).toBeInTheDocument();
  });

  it("stays silent without per-symbol data", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={[]}
        signals={{ ...signals, fix_symbol_counts: null }}
      />,
    );
    expect(screen.queryByRole("button", { name: /Bug history/ })).not.toBeInTheDocument();
  });
});
