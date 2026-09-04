/**
 * The board row as an opportunity, and its triage control.
 *
 * A row used to be a detector output, so a file with fifteen of them filled the
 * screen fifteen times and carried no lifecycle at all. These pin the row's
 * facts, the four triage states, and the two states that are easy to get wrong:
 * an optimistic click that the server rejects, and `addresses_primary_problem`
 * being tri-state rather than a boolean.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";

import { OpportunityRows } from "../../src/refactoring/opportunity-rows";
import {
  addressesPrimaryShort,
  stepSummary,
} from "../../src/refactoring/opportunity";

function opportunity(
  overrides: Partial<RefactoringOpportunity> = {},
): RefactoringOpportunity {
  return {
    opportunity_id: "refop2_row",
    refactoring_model_version: 2,
    status: "open",
    file_path: "packages/core/src/big.py",
    lead_biomarker: "nested_complexity",
    lead_refactoring_type: "split_file",
    addresses_primary_problem: true,
    effort_bucket: "M",
    confidence: "high",
    step_count: 3,
    mechanical_steps: 2,
    judgment_steps: 1,
    evidence_total: 4,
    affected_files_total: 2,
    recoverable_health: 1.4,
    rank_score: 1.45,
    rank_position: 1,
    queue_position: 1,
    rank_factors: {},
    why_ranked: [],
    ...overrides,
  };
}

describe("OpportunityRows", () => {
  it("leads with the file and says how much work it is", () => {
    render(<OpportunityRows opportunities={[opportunity()]} onOpen={() => {}} />);
    expect(screen.getByText("big.py")).toBeTruthy();
    expect(screen.getByText("packages/core/src/big.py")).toBeTruthy();
    expect(screen.getByText("3 steps, 2 mechanical")).toBeTruthy();
    // The lead cause, in words, not a token.
    expect(screen.getByText(/Split File, against nested complexity/)).toBeTruthy();
  });

  it("keeps addresses_primary_problem in three states", () => {
    expect(addressesPrimaryShort(true)).toBe("Main problem");
    expect(addressesPrimaryShort(false)).toBe("Side problem");
    // `null` is "no dominant problem was recorded", which is not "no".
    expect(addressesPrimaryShort(null)).toBe("Lead unknown");
    expect(addressesPrimaryShort(undefined)).toBe("Lead unknown");
  });

  it("says all-mechanical and all-judgment rather than a bare count", () => {
    expect(stepSummary(opportunity({ step_count: 1, mechanical_steps: 0, judgment_steps: 1 })))
      .toBe("1 step, all judgment");
    expect(stepSummary(opportunity({ step_count: 2, mechanical_steps: 2, judgment_steps: 0 })))
      .toBe("2 steps, all mechanical");
  });

  it("offers the four triage states and reports the chosen one", async () => {
    const onStatusChange = vi.fn().mockResolvedValue(undefined);
    render(
      <OpportunityRows
        opportunities={[opportunity()]}
        onOpen={() => {}}
        onStatusChange={onStatusChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /More actions/ }));
    for (const label of ["Open", "Acknowledged", "Resolved", "False positive"]) {
      expect(screen.getByRole("menuitemradio", { name: label })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Acknowledged" }));
    expect(onStatusChange).toHaveBeenCalledWith(
      expect.objectContaining({ opportunity_id: "refop2_row" }),
      "acknowledged",
    );
  });

  it("shows the new state immediately and rolls it back when the server refuses", async () => {
    const onStatusChange = vi.fn().mockRejectedValue(new Error("nope"));
    render(
      <OpportunityRows
        opportunities={[opportunity()]}
        onOpen={() => {}}
        onStatusChange={onStatusChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /More actions/ }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Resolved" }));

    // The optimistic label must not survive a rejected write, and the failure
    // has to be visible rather than leaving a state the server never accepted
    // looking committed.
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    expect(screen.getByText("Could not save")).toBeTruthy();
    expect(screen.queryByText("Resolved")).toBeNull();
  });

  it("marks a triaged row without relying on colour alone", () => {
    render(
      <OpportunityRows
        opportunities={[opportunity({ status: "false_positive" })]}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByText("False positive")).toBeTruthy();
  });

  it("states the status on every row, including an untouched one", () => {
    // It used to appear only once a row left `open`, so the list had no column
    // to scan for what had already been dealt with, and marking a row changed
    // its height. An always-present value is quieter, not louder: `open` is
    // rendered in the tertiary ink and only a decision takes the accent.
    render(<OpportunityRows opportunities={[opportunity()]} onOpen={() => {}} />);
    expect(screen.getByText("Open")).toBeTruthy();
  });

  it("labels its columns, because six unlabelled values is a jumble", () => {
    render(<OpportunityRows opportunities={[opportunity()]} onOpen={() => {}} />);
    for (const heading of ["Type", "File", "Work", "Health", "Status"]) {
      expect(screen.getByText(heading)).toBeTruthy();
    }
  });

  it("hides the triage group entirely when the host cannot write", async () => {
    render(<OpportunityRows opportunities={[opportunity()]} onOpen={() => {}} onAiPrompt={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /More actions/ }));
    expect(screen.queryByRole("menuitemradio")).toBeNull();
    expect(screen.getByRole("menuitem", { name: /Copy prompt/ })).toBeTruthy();
  });
});
