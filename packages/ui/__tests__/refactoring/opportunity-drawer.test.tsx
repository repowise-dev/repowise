/**
 * The drawer when an opportunity resolves but its steps do not load.
 *
 * A host that cannot read the steps still has the opportunity itself, so the
 * drawer keeps its facts and its triage control and says the steps are missing,
 * instead of an empty list under "The steps, in order".
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RefactoringOpportunityDetailResolved } from "@repowise-dev/types/refactoring";

import { OpportunityDrawer } from "../../src/refactoring/opportunity-drawer";

function detail(
  overrides: Partial<RefactoringOpportunityDetailResolved> = {},
): RefactoringOpportunityDetailResolved {
  return {
    resolved: true,
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

    expect(screen.getByRole("radiogroup", { name: "Triage this opportunity" })).toBeTruthy();
    expect(screen.getByText(/The 7 steps for this opportunity could not be loaded/)).toBeTruthy();
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
