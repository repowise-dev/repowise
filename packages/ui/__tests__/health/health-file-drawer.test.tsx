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
    expect(screen.getByRole("button", { name: /File-level signals/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /_run_repo_checks/ })).toBeInTheDocument();
  });

  /** A file-level biomarker that fires per occurrence must not flood the drawer
   *  with sibling rows. `error_handling` reaches 34 on one file in this repo. */
  it("groups file-level markers by biomarker, not into one undifferentiated list", () => {
    const findings: HealthDrawerFinding[] = [
      ...[202, 46, 58].map((line) =>
        finding({
          biomarker_type: "error_handling",
          function_name: null,
          line_start: line,
          line_end: line,
          health_impact: 0.15,
          reason: "broad `except Exception` catches unrelated errors.",
        }),
      ),
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

    // The three error_handling markers collapse into one header carrying their
    // subtotal — not three siblings, and not merged with change_entropy.
    // Scoped to the group header: an expanded group also renders an
    // "About Error handling" InfoTip button.
    const eh = screen.getByRole("button", { name: /Error handling · file-level/i });
    expect(within(eh).getByText(/3 markers/)).toBeInTheDocument();
    expect(within(eh).getByText(/−0\.45/)).toBeInTheDocument();
    // The lone change_entropy marker stays in the pooled bucket rather than
    // earning a group of its own.
    expect(screen.getByRole("button", { name: /File-level signals/ })).toBeInTheDocument();
  });

  it("renders and links the line for a file-level marker that has no function", () => {
    const findings: HealthDrawerFinding[] = [
      finding({
        biomarker_type: "error_handling",
        function_name: null,
        line_start: 202,
        line_end: 202,
        health_impact: 0.15,
        reason: "broad `except Exception` catches unrelated errors.",
      }),
    ];
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={findings}
        fileViewHrefFor={(line) => `/files/doctor_cmd.py#L${line}`}
      />,
    );
    // Gating the anchor on function_name hid the only field distinguishing one
    // error_handling marker from the next, which is what made them read as
    // duplicates.
    const link = screen.getByRole("link", { name: /line 202/ });
    expect(link).toHaveAttribute("href", "/files/doctor_cmd.py#L202");
  });

  it("marks a file-level group as capped when the server says its category shed weight", () => {
    const findings: HealthDrawerFinding[] = [1, 2, 3].map((line) =>
      finding({
        biomarker_type: "error_handling",
        function_name: null,
        line_start: line,
        health_impact: 0.1,
        reason: "broad `except Exception` catches unrelated errors.",
      }),
    );
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={findings}
        breakdown={{
          score: 9.7,
          total_deduction: 0.3,
          categories: [
            {
              category: "error_handling",
              cap: 0.5,
              raw_deduction: 5.1,
              applied_deduction: 0.3,
              capped: true,
              finding_count: 3,
              findings: [],
            },
          ],
        }}
      />,
    );
    // Scoped to the group header: an expanded group also renders an
    // "About Error handling" InfoTip button.
    const eh = screen.getByRole("button", { name: /Error handling · file-level/i });
    expect(within(eh).getByText("capped")).toBeInTheDocument();
  });

  it("does not claim 'capped' when other biomarkers share the capped category", () => {
    // `nested_complexity` and `brain_method` both sit in structural_complexity,
    // so neither group owns the ceiling and neither may name it.
    const findings: HealthDrawerFinding[] = [
      finding({
        biomarker_type: "nested_complexity",
        function_name: null,
        line_start: 5,
        health_impact: 0.2,
        reason: "Deeply nested control flow.",
      }),
      finding({
        biomarker_type: "nested_complexity",
        function_name: null,
        line_start: 9,
        health_impact: 0.2,
        reason: "Deeply nested control flow.",
      }),
      finding({
        biomarker_type: "brain_method",
        function_name: null,
        line_start: 12,
        health_impact: 0.2,
        reason: "Oversized, deeply-nested function.",
      }),
      finding({
        biomarker_type: "brain_method",
        function_name: null,
        line_start: 20,
        health_impact: 0.2,
        reason: "Oversized, deeply-nested function.",
      }),
    ];
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={findings}
        breakdown={{
          score: 9.5,
          total_deduction: 0.5,
          categories: [
            {
              category: "structural_complexity",
              cap: 4.0,
              raw_deduction: 9.1,
              applied_deduction: 4.0,
              capped: true,
              finding_count: 4,
              findings: [],
            },
          ],
        }}
      />,
    );
    for (const label of [/Nested complexity · file-level/i, /Brain method · file-level/i]) {
      expect(within(screen.getByRole("button", { name: label })).queryByText("capped"))
        .not.toBeInTheDocument();
    }
  });

  /** The regression an adversarial review caught: splitting every file-level
   *  biomarker out turned one collapsed row into several EXPANDED ones,
   *  because singleton groups render expanded. 53% of files in this repo carry
   *  2+ distinct one-off file-level markers. One-offs must stay pooled. */
  it("pools one-off file-level markers instead of giving each its own group", () => {
    const findings: HealthDrawerFinding[] = [
      finding({
        biomarker_type: "dry_violation",
        function_name: null,
        line_start: null,
        health_impact: 0.4,
        reason: "Duplicated block.",
      }),
      finding({
        biomarker_type: "change_entropy",
        function_name: null,
        line_start: null,
        health_impact: 0.3,
        reason: "File changes touch many unrelated concerns.",
      }),
      finding({
        biomarker_type: "co_change_scatter",
        function_name: null,
        line_start: null,
        health_impact: 0.2,
        reason: "Co-changes scatter widely.",
      }),
    ];
    render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={findings} />,
    );
    const pooled = screen.getByRole("button", { name: /File-level signals/ });
    expect(within(pooled).getByText(/3 markers/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Dry violation · file-level/i }),
    ).not.toBeInTheDocument();
  });

  it("splits out a repeating file-level biomarker but leaves the one-offs pooled", () => {
    const findings: HealthDrawerFinding[] = [
      ...[10, 20, 30].map((line) =>
        finding({
          biomarker_type: "error_handling",
          function_name: null,
          line_start: line,
          health_impact: 0.05,
          reason: "broad `except Exception` catches unrelated errors.",
        }),
      ),
      finding({
        biomarker_type: "dry_violation",
        function_name: null,
        line_start: null,
        health_impact: 0.4,
        reason: "Duplicated block.",
      }),
      finding({
        biomarker_type: "change_entropy",
        function_name: null,
        line_start: null,
        health_impact: 0.3,
        reason: "File changes touch many unrelated concerns.",
      }),
    ];
    render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={findings} />,
    );
    const eh = screen.getByRole("button", { name: /Error handling · file-level/i });
    expect(within(eh).getByText(/3 markers/)).toBeInTheDocument();
    const pooled = screen.getByRole("button", { name: /File-level signals/ });
    expect(within(pooled).getByText(/2 markers/)).toBeInTheDocument();
  });

  /** A C++/Rust function can legitimately be named `file::read`, so the group
   *  key must not be a collidable string prefix. */
  it("keeps a function named like the file-level sentinel in its function group", () => {
    const findings: HealthDrawerFinding[] = [
      finding({ function_name: "file::read", biomarker_type: "brain_method" }),
      finding({ function_name: "file::read", biomarker_type: "nested_complexity" }),
    ];
    render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={findings} />,
    );
    const group = screen.getByRole("button", { name: /file::read/ });
    expect(within(group).getByText(/2 markers/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /· file-level/i }),
    ).not.toBeInTheDocument();
  });
});

describe("HealthFileDrawer metrics", () => {
  /** Read one cell of the metric list by its label, so these assertions do not
   *  move every time another field joins the grid. */
  function cellValue(label: string): string {
    const dt = screen.getByText(label);
    return dt.parentElement?.querySelector("dd")?.textContent?.trim() ?? "";
  }

  it("says 'not measured' instead of 0 when structural counters are absent", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ max_ccn: null, max_nesting: null, nloc: null })}
      />,
    );
    expect(cellValue("Max CCN")).toBe("not measured");
    expect(cellValue("Nesting")).toBe("not measured");
    expect(cellValue("NLOC")).toBe("not measured");
  });

  it("still renders real zero values as numbers", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ max_ccn: 0, max_nesting: 0, nloc: 12 })}
      />,
    );
    expect(cellValue("Max CCN")).toBe("0");
    expect(cellValue("Nesting")).toBe("0");
    expect(cellValue("NLOC")).toBe("12");
  });

  it("applies the same rule to the pillar scores and the percentages", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={metric()} />);
    // An unscored pillar is not a zero-risk pillar, and no coverage data is not
    // 0% coverage.
    expect(cellValue("Maintainability")).toBe("not measured");
    expect(cellValue("Performance")).toBe("not measured");
    expect(cellValue("Coverage")).toBe("not measured");
    expect(cellValue("Duplication")).toBe("not measured");
  });

  it("leads with the file's own score and band", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={metric({ score: 1.0 })} />);
    expect(screen.getByText("1.0")).toBeInTheDocument();
    expect(screen.getByText("Critical")).toBeInTheDocument();
  });

  it("offers one link to the full page", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        permalinkHref="/repos/r1/files/a.py?tab=health"
      />,
    );
    const links = screen.getAllByRole("link", { name: /open full page/i });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/repos/r1/files/a.py?tab=health");
  });

  it("falls back to the file view when there is no permalink", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        fileViewHref="/repos/r1/files/a.py"
      />,
    );
    expect(screen.getByRole("link", { name: /open full page/i })).toHaveAttribute(
      "href",
      "/repos/r1/files/a.py",
    );
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
