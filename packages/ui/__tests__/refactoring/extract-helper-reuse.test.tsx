/**
 * An Extract Helper plan that reuses an existing function shows that function
 * and each site's action, not a new `helper()`.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { PlanDetail } from "../../src/refactoring/plan-detail";
import { RefactoringDrawer } from "../../src/refactoring/refactoring-drawer";
import { planWins } from "../../src/refactoring/types";

const plan = {
  id: "plan-reuse",
  refactoring_type: "extract_helper",
  file_path: "pkg/b.py",
  target_symbol: "pkg/b.py::run",
  line_start: 6,
  line_end: 11,
  plan: {
    occurrences: [
      { file: "pkg/b.py", line_start: 6, line_end: 11 },
      { file: "pkg/util.py", line_start: 4, line_end: 11 },
    ],
    suggested_name: "normalize_helper",
    reuse: {
      existing_symbol: "pkg/util.py::normalize",
      file: "pkg/util.py",
      span: { start: 4, end: 11 },
      reason: "`normalize` already holds this block; the other 2 sites repeat its body line for line.",
      sites: [
        {
          file: "pkg/b.py",
          span: { start: 6, end: 11 },
          action: "replace_with_call",
          new_text: "return normalize(text, sep)",
          replaces: null,
        },
        {
          file: "pkg/c.py",
          span: { start: 3, end: 9 },
          action: "delete",
          new_text: null,
          replaces: "pkg/c.py::normalize",
        },
      ],
    },
  },
  evidence: { duplicated_lines: 6 },
  impact_delta: 0,
  effort_bucket: "S",
  blast_radius: {},
  confidence: "high",
  source_biomarker: "duplication",
  rank_score: 1,
} satisfies RefactoringPlan;

describe("Extract Helper reuse detail", () => {
  it("names the existing function and each site's call or delete", () => {
    render(<PlanDetail plan={plan} />);
    expect(screen.getByText("normalize()")).toBeTruthy();
    expect(screen.getByText("return normalize(text, sep)")).toBeTruthy();
    expect(screen.getByText(/Delete copy, lines 3–9/)).toBeTruthy();
    expect(screen.queryByText(/normalize_helper/)).toBeNull();
    expect(screen.queryByText(/Extract it once/)).toBeNull();
  });
});

describe("Extract Helper reuse in the drawer", () => {
  it("says the existing function is called, not a new helper", () => {
    render(<RefactoringDrawer plan={plan} open onOpenChange={() => {}} />);
    expect(screen.getByText(/Call the function that already holds this block/)).toBeTruthy();
    expect(screen.queryByText(/into one shared helper/)).toBeNull();
    expect(planWins(plan).map((w) => w.label)).toContain("2 duplicate copies collapsed into normalize()");
  });
});
