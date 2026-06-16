import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { ChurnComplexityPoint } from "@repowise-dev/types/health";
import { ChurnComplexityQuadrant } from "../../src/health/churn-complexity-quadrant.js";

function pt(partial: Partial<ChurnComplexityPoint> & { file_path: string }): ChurnComplexityPoint {
  return {
    commit_count_90d: 5,
    max_ccn: 8,
    nloc: 100,
    score: 7,
    churn_percentile: 50,
    ...partial,
  };
}

describe("ChurnComplexityQuadrant", () => {
  it("renders the empty state when there are no points", () => {
    render(<ChurnComplexityQuadrant points={[]} />);
    expect(screen.getByText(/needs git history/i)).toBeInTheDocument();
  });

  it("filters out files with no recent churn", () => {
    render(<ChurnComplexityQuadrant points={[pt({ file_path: "a.py", commit_count_90d: 0 })]} />);
    // Only the zero-churn file was passed → degenerates to the empty state.
    expect(screen.getByText(/needs git history/i)).toBeInTheDocument();
  });

  it("plots points and labels the refactor (danger) zone", () => {
    render(
      <ChurnComplexityQuadrant
        points={[
          pt({ file_path: "hot.py", commit_count_90d: 30, max_ccn: 40, score: 3 }),
          pt({ file_path: "calm.py", commit_count_90d: 2, max_ccn: 2, score: 9 }),
        ]}
      />,
    );
    expect(screen.getByText("Refactor zone")).toBeInTheDocument();
    expect(screen.getByText(/2 files/)).toBeInTheDocument();
  });

  it("invokes onSelect when a dot is clicked", () => {
    const onSelect = vi.fn();
    const { container } = render(
      <ChurnComplexityQuadrant
        points={[pt({ file_path: "hot.py", commit_count_90d: 30, max_ccn: 40 })]}
        onSelect={onSelect}
      />,
    );
    const circle = container.querySelector("circle");
    expect(circle).not.toBeNull();
    fireEvent.click(circle!);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ file_path: "hot.py" }));
  });
});
