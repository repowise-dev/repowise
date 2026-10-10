import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RefactoringRollupAvailable } from "@repowise-dev/types/refactoring";

import { RefactoringLede } from "../../src/refactoring/refactoring-lede";

const summary: RefactoringRollupAvailable = {
  status: "available",
  opportunities_total: 669,
  files_total: 669,
  steps_total: 1127,
  mechanical_steps_total: 679,
  judgment_steps_total: 448,
  by_lead_type: { extract_method: 551, split_file: 118 },
  by_effort: { S: 300, XL: 369 },
  by_confidence: {},
  by_status: { open: 669 },
  addresses_primary_problem: { yes: 1, no: 2, unknown: 0 },
  lead: null,
  refactoring_model_version: 2,
  analyzed_commit: null,
};

const facets = { effort: { S: 41 }, lead_type: { split_file: 7, extract_method: 70 } };

describe("RefactoringLede", () => {
  it("reads as two lines with chips counted from the facets", () => {
    const onToggleQuickWins = vi.fn();
    const onSeeStructural = vi.fn();
    render(
      <RefactoringLede
        summary={summary}
        facets={facets}
        onToggleQuickWins={onToggleQuickWins}
        onSeeStructural={onSeeStructural}
      />,
    );
    expect(screen.getByText("118 change a file's shape; 551 are local.")).toBeTruthy();
    expect(screen.getByText("679 of 1,127 steps are mechanical,")).toBeTruthy();
    expect(screen.queryByText("Files with work")).toBeNull();

    const quick = screen.getByRole("button", { name: "Quick wins 41" });
    expect(quick.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(quick);
    expect(onToggleQuickWins).toHaveBeenCalledOnce();

    // A jump to the structural tab, not a filter that could read as pressed.
    const structural = screen.getByRole("button", { name: "See the 7 structural" });
    expect(structural.hasAttribute("aria-pressed")).toBe(false);
    fireEvent.click(structural);
    expect(onSeeStructural).toHaveBeenCalledOnce();
  });

  it("marks Quick wins pressed while the list is narrowed to small effort", () => {
    render(
      <RefactoringLede
        summary={summary}
        facets={facets}
        quickWinsActive
        onToggleQuickWins={() => {}}
      />,
    );
    const quick = screen.getByRole("button", { name: "Quick wins 41" });
    expect(quick.getAttribute("aria-pressed")).toBe("true");
  });

  it("says all of it is local when nothing is structural", () => {
    render(
      <RefactoringLede
        summary={{ ...summary, by_lead_type: { extract_method: 669 } }}
        facets={{ effort: { S: 41 }, lead_type: { extract_method: 70 } }}
        onToggleQuickWins={() => {}}
        onSeeStructural={() => {}}
      />,
    );
    expect(screen.getByText("All of it is local.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /structural/ })).toBeNull();
  });

  it("drops the chips without facets", () => {
    render(<RefactoringLede summary={summary} onToggleQuickWins={() => {}} />);
    expect(screen.queryByRole("group", { name: "Filter the list" })).toBeNull();
  });
});
