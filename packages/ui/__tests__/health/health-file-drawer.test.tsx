import { describe, it, expect } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import {
  HealthFileDrawer,
  type HealthDrawerFinding,
} from "../../src/health/health-file-drawer.js";
import type { PerformanceOpportunity } from "@repowise-dev/types/health";
import {
  drawerFinding as finding,
  drawerMetric as metric,
  openSection,
} from "./drawer-test-utils.js";

/** Three heavier issues of other kinds, so the findings under test land in
 *  Other issues rather than leading What's wrong. */
function lead(): HealthDrawerFinding[] {
  return ["large_method", "complex_method", "primitive_obsession"].map((biomarker_type, i) =>
    finding({ biomarker_type, function_name: "lead_fn", health_impact: 50 + i }),
  );
}

describe("HealthFileDrawer finding grouping", () => {
  it("collapses many markers on one function into a single group header", () => {
    const findings: HealthDrawerFinding[] = [
      finding({ biomarker_type: "brain_method", health_impact: 3.0 }),
      finding({ biomarker_type: "long_method", health_impact: 2.0 }),
      finding({ biomarker_type: "deep_nesting", health_impact: 1.0 }),
    ];
    render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={[...lead(), ...findings]} />,
    );
    openSection(/Other issues/);

    // One group header names the function + its worst marker, and sums impact.
    const header = screen.getByRole("button", { name: /_run_repo_checks/ });
    expect(within(header).getByText(/3 issues/)).toBeInTheDocument();
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
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={[...lead(), ...findings]} />,
    );
    openSection(/Other issues/);
    expect(screen.getByRole("button", { name: /Across the file/ })).toBeInTheDocument();
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
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={[...lead(), ...findings]} />,
    );
    openSection(/Other issues/);

    // The three error_handling markers collapse into one header carrying their
    // subtotal — not three siblings, and not merged with change_entropy.
    // Scoped to the group header: an expanded group also renders an
    // "About Error handling" InfoTip button.
    const eh = screen.getByRole("button", { name: /Error handling · across the file/i });
    expect(within(eh).getByText(/3 issues/)).toBeInTheDocument();
    expect(within(eh).getByText(/−0\.45/)).toBeInTheDocument();
    // The lone change_entropy marker stays in the pooled bucket rather than
    // earning a group of its own.
    expect(screen.getByRole("button", { name: /Across the file/ })).toBeInTheDocument();
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
    // duplicates. The link is in the opened row.
    fireEvent.click(screen.getByRole("button", { name: /Errors caught too broadly/ }));
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
        findings={[...lead(), ...findings]}
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
    openSection(/Other issues/);
    // Scoped to the group header: an expanded group also renders an
    // "About Error handling" InfoTip button.
    const eh = screen.getByRole("button", { name: /Error handling · across the file/i });
    expect(within(eh).getByText("at limit")).toBeInTheDocument();
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
        findings={[...lead(), ...findings]}
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
    openSection(/Other issues/);
    for (const label of [/Deep nesting · across the file/i, /Brain method · across the file/i]) {
      expect(within(screen.getByRole("button", { name: label })).queryByText("at limit"))
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
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={[...lead(), ...findings]} />,
    );
    openSection(/Other issues/);
    const pooled = screen.getByRole("button", { name: /Across the file/ });
    expect(within(pooled).getByText(/3 issues/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Dry violation · across the file/i }),
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
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={[...lead(), ...findings]} />,
    );
    openSection(/Other issues/);
    const eh = screen.getByRole("button", { name: /Error handling · across the file/i });
    expect(within(eh).getByText(/3 issues/)).toBeInTheDocument();
    const pooled = screen.getByRole("button", { name: /Across the file/ });
    expect(within(pooled).getByText(/2 issues/)).toBeInTheDocument();
  });

  /** A C++/Rust function can legitimately be named `file::read`, so the group
   *  key must not be a collidable string prefix. */
  it("keeps a function named like the file-level sentinel in its function group", () => {
    const findings: HealthDrawerFinding[] = [
      finding({ function_name: "file::read", biomarker_type: "brain_method" }),
      finding({ function_name: "file::read", biomarker_type: "nested_complexity" }),
    ];
    render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} findings={[...lead(), ...findings]} />,
    );
    openSection(/Other issues/);
    const group = screen.getByRole("button", { name: /file::read/ });
    expect(within(group).getByText(/2 issues/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /· across the file/i }),
    ).not.toBeInTheDocument();
  });
});

describe("HealthFileDrawer metrics", () => {
  /** Read one cell of the metric list by its label, so these assertions do not
   *  move every time another field joins the grid. */
  function cellValue(label: string): string {
    // The Details grid's cell, not the vital-signs strip's word of the same name.
    const cell = screen
      .getAllByText(label)
      .find((el) => el.closest("dt"))
      ?.closest("div");
    return cell?.querySelector("dd")?.textContent?.trim() ?? "";
  }

  it("says 'not measured' instead of 0 when structural counters are absent", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ max_ccn: null, max_nesting: null, nloc: null })}
      />,
    );
    // The collapsed toggle already says so, before it is opened.
    openSection(/Details/);
    expect(cellValue("Complexity")).toBe("not measured");
    expect(cellValue("Nesting depth")).toBe("not measured");
    expect(cellValue("Lines of code")).toBe("not measured");
  });

  it("still renders real zero values as numbers", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ max_ccn: 0, max_nesting: 0, nloc: 12 })}
      />,
    );
    openSection(/Details/);
    expect(cellValue("Complexity")).toBe("0");
    expect(cellValue("Nesting depth")).toBe("0");
    expect(cellValue("Lines of code")).toBe("12");
  });

  it("applies the same rule to the pillar scores and the percentages", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={metric()} />);
    openSection(/Details/);
    // An unscored pillar is not a zero-risk pillar, and no coverage data is not
    // 0% coverage.
    expect(cellValue("Maintainability")).toBe("not measured");
    expect(cellValue("Performance")).toBe("not measured");
    expect(cellValue("Line coverage")).toBe("not measured");
    expect(cellValue("Duplication")).toBe("not measured");
  });

  it("leads with the file's own score and a canonical band", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={metric({ score: 1.0 })} />);
    expect(screen.getByText("1.0")).toBeInTheDocument();
    // The shared band word, never a local one: this pill sits beside marks
    // that all derive from `bandForScore`.
    expect(screen.getByText("At risk")).toBeInTheDocument();
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

  it("files the fix counts under History & ownership, with the last fix on the toggle", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={[]}
        signals={signals}
      />,
    );

    const toggle = screen.getByRole("button", { name: /History & ownership/ });
    // Recency rides on the toggle itself: the counts are collapsed, the "is
    // this still happening?" answer is not.
    // The hint names what is inside rather than restating the counts.
    expect(toggle).toHaveTextContent("change history · where fixes landed");
    // Collapsed by default, because "where do the bugs cluster" is not a question
    // every reader of the drawer has.
    expect(screen.queryByText("run_update")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    // The process row carries the count and recency once; the per-symbol
    // list under it does not repeat them.
    expect(screen.getByText("8 bug-fixes")).toBeInTheDocument();
    expect(screen.getByText("Where the fixes landed")).toBeInTheDocument();
    expect(screen.getByText("run_update")).toBeInTheDocument();
    expect(screen.getByText("4 fixes")).toBeInTheDocument();
    expect(screen.getByText("2 fixes")).toBeInTheDocument();
    // The line-range mapping is approximate and says so, rather than letting
    // the counts read as exact.
    expect(screen.getByText(/lines move/)).toBeInTheDocument();
  });

  it("drops the per-symbol list without per-symbol data", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={[]}
        signals={{ ...signals, fix_symbol_counts: null }}
      />,
    );
    openSection(/History & ownership/);
    expect(screen.queryByText("Where the fixes landed")).not.toBeInTheDocument();
  });
});

describe("HealthFileDrawer under the performance lens", () => {
  function cause(
    id: string,
    partial: Partial<PerformanceOpportunity> = {},
  ): PerformanceOpportunity {
    return {
      opportunity_id: id,
      performance_model_version: 2,
      biomarker_type: "serial_await_in_loop",
      biomarker_types: ["serial_await_in_loop"],
      boundary_kind: "db",
      execution_context: "production",
      terminal_sink: null,
      shared_path_suffix: [],
      intervention_symbol: null,
      file_path: "packages/cli/doctor_cmd.py",
      resource_fingerprints: [],
      affected_call_sites_total: 1,
      affected_files_total: 1,
      observations_total: 1,
      evidence: [],
      evidence_truncated: false,
      evidence_total: 1,
      evidence_emitted: 1,
      reliable_entry_reachability: null,
      provenance: "direct",
      confidence: "high",
      facets: {
        actionability_confidence: "high",
        exposure: "unknown",
        amplification: "unknown",
        leverage: "single_site",
        change_risk: "low",
      },
      actionability_state: "plan_ready",
      actionability_reason: "proven_strategy",
      prerequisites: [],
      rank_score: 10,
      rank_position: 0,
      rank_factors: {},
      why_ranked: [],
      fix: null,
      plan_id: "plan-1",
      plan_status: "available",
      plan_reason: "",
      ...partial,
    } as PerformanceOpportunity;
  }

  const located = (id: string, fn: string, line: number) =>
    cause(id, {
      evidence: [
        {
          finding_id: `finding_${id}`,
          file_path: "packages/cli/doctor_cmd.py",
          biomarker_type: "serial_await_in_loop",
          function_name: fn,
          line_start: line,
          line_end: line,
          reason: "a database call is awaited serially in a loop",
          path: [],
          provenance: "direct",
        },
      ],
    });

  it("leads with the causes rather than with the defect score", () => {
    const { getByText } = render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        lens="performance"
        performance={{ items: [cause("perf2_a")], total: 1 }}
      />,
    );
    expect(getByText("Performance causes")).toBeInTheDocument();
  });

  it("tells two causes of one marker apart by where they fire", () => {
    // Thirty-six causes of a single marker on one file is real here. Titling
    // them by the marker alone renders thirty-six identical rows.
    const { getByText } = render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        lens="performance"
        performance={{
          items: [located("perf2_a", "sweep_pages", 1312), located("perf2_b", "sweep_cycles", 1218)],
          total: 2,
        }}
      />,
    );
    expect(getByText("sweep_pages:1312")).toBeInTheDocument();
    expect(getByText("sweep_cycles:1218")).toBeInTheDocument();
  });

  it("counts the repository's causes, not the slice it was handed", () => {
    const { getByText } = render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        lens="performance"
        performance={{ items: [cause("perf2_a"), cause("perf2_b")], total: 36 }}
      />,
    );
    // The figures sit in their own tabular-nums spans, so read the sentence.
    const summary = getByText(/open cause/).textContent ?? "";
    expect(summary).toContain("36 open causes");
    expect(summary).toContain("2 shown");
  });

  it("says nothing about performance under another lens", () => {
    const { queryByText } = render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        performance={{ items: [cause("perf2_a")], total: 1 }}
      />,
    );
    expect(queryByText("Performance causes")).not.toBeInTheDocument();
  });

  it("distinguishes a host that did not ask from a file with no cause", () => {
    const notWired = render(
      <HealthFileDrawer open onClose={() => {}} metric={metric()} lens="performance" />,
    );
    expect(notWired.getByText(/no performance data wired up/)).toBeInTheDocument();

    const clean = render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        lens="performance"
        performance={{ items: [], total: 0 }}
      />,
    );
    // And refuses the reading that a clear detector run means the file is fast.
    const empty = clean.getByText(/No open cause names this file/).textContent ?? "";
    expect(empty).toContain("not a measurement that it is fast");
  });
});

describe("HealthFileDrawer verdict and history", () => {
  it("passes over a history lead for the strongest code-shape finding", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ primary_biomarker: "co_change_scatter", primary_reason: "edits scatter" })}
        findings={[
          finding({ biomarker_type: "co_change_scatter", health_impact: 1.8 }),
          finding({ biomarker_type: "brain_method", health_impact: 0.4 }),
        ]}
      />,
    );
    // The server's lead is the history marker; the drawer must not present it
    // as the thing to fix.
    const rows = within(screen.getByRole("region", { name: "What's wrong" })).getAllByRole("listitem");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Function does too much");
    expect(screen.queryByText(/Edits ripple to other files/)).not.toBeInTheDocument();
  });

  it("says so when a file's whole deduction is history", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ primary_biomarker: "co_change_scatter", primary_reason: "edits scatter" })}
        findings={[finding({ biomarker_type: "co_change_scatter", health_impact: 1.8 })]}
      />,
    );
    expect(screen.getByText(/score comes from change history/)).toBeInTheDocument();
  });

  it("marks a history finding as a watch item, not a pillar", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric()}
        findings={[finding({ biomarker_type: "prior_defect", health_impact: 1.0 })]}
      />,
    );
    // A history marker is never offered as something to fix.
    expect(screen.queryByRole("region", { name: "What's wrong" })).not.toBeInTheDocument();
    openSection(/All issues/);
    expect(screen.getAllByText("Watch").length).toBeGreaterThan(0);
    // No pillar chip: the pillar colours mark where work belongs, and this is
    // not work. The title is what distinguishes the chip from the score label.
    expect(document.querySelector('[title="Code health pillar"]')).toBeNull();
  });
});

describe("HealthFileDrawer: a file with no score", () => {
  it("says it was not analysed instead of printing a score and a band", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={metric({ file_path: "src/Big.php", score: null, max_ccn: null, max_nesting: null })}
        findings={[]}
      />,
    );
    expect(screen.getByText(/Not analysed\. No detector for this language\./)).toBeInTheDocument();
    expect(screen.queryByText("out of 10")).toBeNull();
  });
});
