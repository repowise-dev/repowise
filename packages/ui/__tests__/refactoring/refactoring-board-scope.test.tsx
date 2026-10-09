import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";

import {
  RefactoringBoard,
  type RefactoringBoardServerState,
} from "../../src/refactoring/refactoring-board";

const opportunity: RefactoringOpportunity = {
  opportunity_id: "refop2_scope",
  refactoring_model_version: 2,
  status: "open",
  file_path: "src/core.py",
  lead_biomarker: "complex_method",
  lead_refactoring_type: "extract_method",
  addresses_primary_problem: true,
  effort_bucket: "S",
  confidence: "high",
  step_count: 1,
  mechanical_steps: 1,
  judgment_steps: 0,
  evidence_total: 0,
  affected_files_total: 1,
  recoverable_health: 1.2,
  rank_score: 1.2,
  rank_position: 1,
  queue_position: 1,
  rank_factors: {},
  why_ranked: [],
};

const state: RefactoringBoardServerState = {
  query: "",
  order: "queue",
  status: "open",
  effort: null,
  confidence: null,
  mechanicalOnly: false,
  scope: "fix_first",
  appliedScope: "fix_first",
  hidden: { total: 3, by_reason: { test: 2, below_min_worth: 1 } },
  total: 1,
  offset: 0,
  nextOffset: null,
};

function board(over: Partial<RefactoringBoardServerState> = {}, onChange = vi.fn()) {
  render(
    <RefactoringBoard
      opportunities={[opportunity]}
      showLede={false}
      serverState={{ ...state, ...over }}
      onServerStateChange={onChange}
    />,
  );
  return onChange;
}

describe("RefactoringBoard scope", () => {
  it("says how many are worth doing and how many the inventory adds, with reasons", () => {
    board();
    expect(
      screen.getByRole("heading", {
        name: "Showing 1 worth doing; 3 more in the full inventory",
      }),
    ).toBeTruthy();
    expect(screen.getByText("Left out: 2 in tests, 1 below the worth floor.")).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Worth doing" }).getAttribute("aria-checked")).toBe(
      "true",
    );
  });

  it("switches to the full inventory from the first page", () => {
    const onChange = board();
    fireEvent.click(screen.getByRole("radio", { name: "Full inventory" }));
    expect(onChange).toHaveBeenCalledWith({ scope: "all", offset: 0 });
  });

  it("counts the inventory in its own words", () => {
    board({ scope: "all", appliedScope: "all", hidden: null, total: 4 });
    expect(screen.getByText(/4 open\s+opportunities/)).toBeTruthy();
    expect(screen.queryByText(/worth doing;/)).toBeNull();
  });

  it("disables Worth doing for a triaged status, with the reason", () => {
    board({ status: "resolved", appliedScope: "all", hidden: null });
    const worth = screen.getByRole("radio", { name: /Worth doing/ });
    expect(worth.getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByText("Fix first ranks open opportunities only.")).toBeTruthy();
  });

  it("says why an empty page is empty when the default left everything out", () => {
    render(
      <RefactoringBoard
        opportunities={[]}
        showLede={false}
        summary={null}
        serverState={{ ...state, total: 0, query: "tests/" }}
        onServerStateChange={() => {}}
      />,
    );
    expect(
      screen.getByText("None of these is worth doing first. 3 more are in the full inventory."),
    ).toBeTruthy();
  });
});

describe("RefactoringBoard without a host scope", () => {
  it("shows no Scope control and no hidden-counts note", () => {
    board({ scope: undefined, appliedScope: undefined, hidden: undefined, total: 1 });
    expect(screen.queryByRole("radio", { name: "Worth doing" })).toBeNull();
    expect(screen.queryByRole("radio", { name: "Full inventory" })).toBeNull();
    expect(screen.queryByText(/more in the full inventory/)).toBeNull();
  });
});
