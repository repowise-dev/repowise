import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { PriorityExplanation } from "../../src/refactoring/priority-explanation";
import { PlanDetail } from "../../src/refactoring/plan-detail";
import { ValidationSummary } from "../../src/refactoring/validation-summary";
import { RefactoringBoard } from "../../src/refactoring/refactoring-board";

const base = {
  id: "plan-1",
  refactoring_type: "performance_fix",
  file_path: "src/shared.py",
  target_symbol: "src/shared.py::load",
  line_start: null,
  line_end: null,
  plan: {},
  evidence: {},
  impact_delta: 0,
  effort_bucket: "M",
  blast_radius: {},
  confidence: "medium",
  source_biomarker: "io_in_loop",
  rank_score: 8.25,
} satisfies RefactoringPlan;

describe("Phase 4 structured plan contract", () => {
  it("explains canonical priority components without treating blast as benefit", () => {
    render(<PriorityExplanation plan={{ ...base, benefit: 4, leverage: 3, cost: 2, risk: 5 }} />);
    expect(screen.getByText("8.2500")).toBeTruthy();
    expect(screen.getByText("Blast radius, evidence strength, and validation gaps.")).toBeTruthy();
    expect(screen.getByText("Health recovered or detector-native gain.")).toBeTruthy();
  });

  it("keeps true validation totals and labels capped lists", () => {
    render(
      <ValidationSummary
        validation={{
          basis: "inferred",
          via: "call-graph",
          total: 9,
          tests: ["tests/a.py", "tests/b.py"],
          truncated: true,
          affected_files: ["src/shared.py"],
          affected_symbols: ["src/shared.py::load"],
          commands: ["pytest tests"],
          targets: [],
        }}
      />,
    );
    expect(screen.getByText(/9 guarding tests; 2 shown/)).toBeTruthy();
    expect(screen.getByText(/capped display rows/)).toBeTruthy();
  });

  it("degrades honestly when optional fields are missing from an older server", () => {
    const { rerender } = render(<PriorityExplanation plan={base} />);
    expect(screen.getByText(/server predates the detailed priority components/)).toBeTruthy();
    rerender(<ValidationSummary />);
    expect(screen.getByText(/unavailable from this older server/)).toBeTruthy();
  });

  it("renders a server-filtered performance plan without re-ranking it locally", () => {
    render(
      <RefactoringBoard
        plans={[base]}
        showLede={false}
        serverState={{
          query: "",
          sort: "canonical",
          efforts: [],
          confidences: [],
          total: 1,
          offset: 0,
          nextOffset: null,
        }}
        onServerStateChange={() => {}}
      />,
    );
    expect(screen.getByText("Performance")).toBeTruthy();
    expect(screen.getByText("1 plan")).toBeTruthy();
    expect(screen.getByText("src/shared.py::load")).toBeTruthy();
  });

  it("links the shared intervention and does not mislabel observations as paths", () => {
    render(
      <PlanDetail
        plan={{
          ...base,
          line_start: 12,
          plan: {
            strategy: "batch_or_prefetch_io",
            safety: "advisory",
            intervention_symbol: "src/shared.py::load",
            paths: [["src/a.py::run", "src/shared.py::load"]],
            paths_total: 6,
            evidence_truncated: true,
            affected_locations: [],
            affected_locations_total: 0,
          },
        }}
        fileHref={(path, line) => `/files/${path}${line ? `?line=${line}` : ""}`}
      />,
    );

    expect(screen.getByRole("link", { name: /src\/shared.py::load/ }).getAttribute("href")).toBe(
      "/files/src/shared.py?line=12",
    );
    expect(screen.getByText(/1 resolved path shown; additional observations remain/)).toBeTruthy();
    expect(screen.queryByText(/of 6 paths/)).toBeNull();
  });
});
