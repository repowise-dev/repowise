/**
 * The drawer when an opportunity resolves but its steps do not load.
 *
 * A host that cannot read the steps still has the opportunity itself, so the
 * drawer keeps its facts and its triage control and says the steps are missing,
 * instead of an empty list under "The steps, in order".
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  OpportunityStep,
  RefactoringOpportunityDetailResolved,
  RefactoringPlan,
} from "@repowise-dev/types/refactoring";

import { OpportunityDrawer } from "../../src/refactoring/opportunity-drawer";

function detail(
  overrides: Partial<RefactoringOpportunityDetailResolved> = {},
): RefactoringOpportunityDetailResolved {
  return {
    found: true,
    opportunity_id: "refop2_drawer",
    refactoring_model_version: 2,
    status: "open",
    file_path: "packages/core/src/big.py",
    lead_biomarker: "nested_complexity",
    lead_refactoring_type: "split_file",
    addresses_primary_problem: true,
    effort_bucket: "M",
    confidence: "high",
    step_count: 7,
    mechanical_steps: 5,
    judgment_steps: 2,
    evidence_total: 0,
    affected_files_total: 1,
    recoverable_health: 1.4,
    rank_score: 1.45,
    rank_position: 1,
    queue_position: 1,
    rank_factors: {},
    why_ranked: [],
    steps: [],
    steps_total: 7,
    steps_emitted: 0,
    evidence: [],
    evidence_emitted: 0,
    evidence_truncated: false,
    affected_files: ["packages/core/src/big.py"],
    validation_profiles: [],
    plans: [],
    next_actions: [],
    ...overrides,
  };
}

function renderDrawer(d: RefactoringOpportunityDetailResolved) {
  render(
    <OpportunityDrawer
      detail={d}
      open
      onOpenChange={() => {}}
      onStatusChange={() => {}}
      onAiPrompt={() => {}}
    />,
  );
}

describe("OpportunityDrawer steps unavailable", () => {
  it("keeps triage and says the steps did not load", () => {
    renderDrawer(detail({ details_status: "unavailable", steps_total: null }));

    expect(screen.getByRole("radiogroup", { name: "Triage this refactoring plan" })).toBeTruthy();
    expect(screen.getByText(/The 7 steps for this refactoring plan could not be loaded/)).toBeTruthy();
    expect(screen.queryByText(/Showing 0 of/)).toBeNull();
    // The prompt is built from the steps, so it is not offered without them.
    expect(screen.queryByRole("button", { name: /Copy prompt/ })).toBeNull();
  });

  it("says nothing about missing steps when they loaded", () => {
    renderDrawer(detail({ details_status: "available", steps_total: 12, steps_emitted: 0 }));

    expect(screen.queryByText(/could not be loaded/)).toBeNull();
    expect(screen.getByText("Showing 0 of 12 steps.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Copy prompt/ })).toBeTruthy();
  });
});

const STEP: OpportunityStep = {
  plan_id: "refac3_step",
  refactoring_type: "extract_method",
  target_symbol: "quick_repo_scan",
  file_path: "packages/core/src/big.py",
  line_start: 3,
  line_end: 4,
  effort_bucket: "S",
  confidence: "high",
  impact_delta: 1.7,
  source_biomarker: "complex_method",
  relocated_by: null,
  applicability: { classification: "mechanical", reasons: [], facts: {}, unknowns: [] },
};

const PLAN = {
  id: "refac3_step",
  refactoring_type: "extract_method",
  file_path: "packages/core/src/big.py",
  target_symbol: "quick_repo_scan",
  line_start: 3,
  line_end: 4,
  plan: {},
  evidence: {},
  impact_delta: 1.7,
  effort_bucket: "S",
  blast_radius: {},
  confidence: "high",
  source_biomarker: "complex_method",
  rank_score: 1,
} as RefactoringPlan;

describe("OpportunityDrawer reach, verification and code", () => {
  it("states the blast radius for every type, importers included when stored", () => {
    renderDrawer(detail({ affected_files_total: 2, dependents: 4 }));
    expect(screen.getByText(/^Touches 2 files; 4 files import it\./)).toBeTruthy();
  });

  it("says when importers were not recorded instead of implying none", () => {
    renderDrawer(detail());
    expect(
      screen.getByText("Touches 1 file; importers not recorded. No guarding tests found."),
    ).toBeTruthy();
  });

  it("always shows Verify, with the explicit message when nothing guards the file", () => {
    renderDrawer(detail());
    const verify = screen.getByRole("button", { name: /Verify/ });
    expect(verify.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(verify);
    expect(screen.getByText(/Write one that pins the current behaviour/)).toBeTruthy();
  });

  it("keeps the agent id collapsed until asked for", () => {
    renderDrawer(detail());
    expect(screen.queryByText(/get_health\(opportunity_id=/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Ask for this by id/ }));
    expect(screen.getByText(/get_health\(opportunity_id=/)).toBeTruthy();
  });

  it("reads the step's span from the file for an inline excerpt", async () => {
    const readSource = vi.fn().mockResolvedValue(["a = 1", "b = 2", "c = 3", "d = 4", "e = 5"].join(String.fromCharCode(10)));
    render(
      <OpportunityDrawer
        detail={detail({ steps: [STEP], steps_emitted: 1, plans: [PLAN] })}
        open
        onOpenChange={() => {}}
        readSource={readSource}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show the code" }));
    expect(await screen.findByText("c = 3")).toBeTruthy();
    expect(screen.getByText("d = 4")).toBeTruthy();
    expect(screen.queryByText("e = 5")).toBeNull();
    expect(readSource).toHaveBeenCalledWith("packages/core/src/big.py");
    // Generation is off, so the diff preview says how it would be drafted.
    expect(screen.getByText(/A diff preview is drafted by a model on request/)).toBeTruthy();
  });

  it("shows an extraction's slice under the helper's signature, not the whole function", async () => {
    const readSource = vi.fn().mockResolvedValue(["def f():", "  a = 1", "  b = 2", "  c = 3", "  return c"].join(String.fromCharCode(10)));
    const plan = {
      ...PLAN,
      line_start: 1,
      line_end: 5,
      plan: { span: { start: 2, end: 3 }, params: [], returns: ["b"], suggested_name: "_setup" },
    } as RefactoringPlan;
    render(
      <OpportunityDrawer
        detail={detail({
          steps: [{ ...STEP, line_start: 1, line_end: 5 }],
          steps_emitted: 1,
          plans: [plan],
        })}
        open
        onOpenChange={() => {}}
        readSource={readSource}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show the code" }));
    expect(screen.getByText("_setup() -> b")).toBeTruthy();
    expect(await screen.findByText("a = 1", { exact: false })).toBeTruthy();
    expect(screen.queryByText("def f():")).toBeNull();
    expect(screen.queryByText("return c", { exact: false })).toBeNull();
  });

  it("offers the model-drafted diff preview when the host turned generation on", () => {
    render(
      <OpportunityDrawer
        detail={detail({ steps: [STEP], steps_emitted: 1, plans: [PLAN] })}
        open
        onOpenChange={() => {}}
        onGenerateCode={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show the code" }));
    expect(screen.getByRole("button", { name: /Generate code/ })).toBeTruthy();
  });
});
