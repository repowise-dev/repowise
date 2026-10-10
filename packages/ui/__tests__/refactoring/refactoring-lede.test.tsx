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

describe("RefactoringLede", () => {
  it("reads as two lines with chips counted from the facets", () => {
    const onQuickWins = vi.fn();
    const onStructural = vi.fn();
    render(
      <RefactoringLede
        summary={summary}
        facets={{ effort: { S: 41 }, lead_type: { split_file: 7, extract_method: 70 } }}
        onQuickWins={onQuickWins}
        onStructural={onStructural}
      />,
    );
    expect(screen.getByText("118 change a file's shape; 551 are local.")).toBeTruthy();
    expect(screen.getByText("679 of 1,127 steps are mechanical,")).toBeTruthy();
    expect(screen.queryByText("Files with work")).toBeNull();

    const quick = screen.getByRole("button", { name: "Quick wins 41" });
    expect(quick.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(quick);
    expect(onQuickWins).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Structural 7" }));
    expect(onStructural).toHaveBeenCalledOnce();
  });

  it("drops the chips without facets", () => {
    render(<RefactoringLede summary={summary} onQuickWins={() => {}} />);
    expect(screen.queryByRole("group", { name: "Filter the list" })).toBeNull();
  });
});
