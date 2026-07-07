import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { DsmCell, DsmMatrix } from "@repowise-dev/types";
import { DsmMatrixView } from "../../src/workspace/dsm/dsm-matrix";

// Must mirror the named constant in dsm-matrix.tsx.
const DSM_RENDER_BUDGET = 60;

function cell(from: string, to: string, present: boolean): DsmCell {
  return {
    from_id: from,
    to_id: to,
    present,
    kind: present ? "http" : null,
    edge_ids: present ? [`${from}->${to}`] : [],
    violation: false,
    cycle: false,
  };
}

/**
 * Build an n×n matrix. `presentPairs` is a set of "i,j" row→col indices that
 * are present; everything else is empty. Services are labelled `svc-<i>`.
 */
function matrix(n: number, presentPairs: Set<string> = new Set()): DsmMatrix {
  const axis = Array.from({ length: n }, (_, i) => `svc-${i}`);
  const labels = axis.slice();
  const cells = axis.map((from, i) =>
    axis.map((to, j) => cell(from, to, presentPairs.has(`${i},${j}`))),
  );
  return { axis, labels, cells };
}

describe("DsmMatrixView render budget", () => {
  it("renders every axis label when n is under the budget", () => {
    const n = 5;
    render(<DsmMatrixView matrix={matrix(n)} />);
    // Each service appears as both a row header and a column header.
    for (let i = 0; i < n; i += 1) {
      expect(screen.getAllByText(`svc-${i}`).length).toBeGreaterThanOrEqual(2);
    }
    // No "showing top" indicator under the budget.
    expect(screen.queryByText(/showing top/i)).not.toBeInTheDocument();
  });

  it("budgets to the most-connected services when n exceeds the budget", () => {
    const n = DSM_RENDER_BUDGET + 10; // 70 services
    // Make the LAST `DSM_RENDER_BUDGET` services the high-degree ones by giving
    // each of them an out-edge to the next, so the first 10 (svc-0..svc-9) have
    // degree 0 and must be dropped.
    const present = new Set<string>();
    for (let i = n - DSM_RENDER_BUDGET; i < n - 1; i += 1) {
      present.add(`${i},${i + 1}`); // svc-i depends on svc-(i+1)
    }
    render(<DsmMatrixView matrix={matrix(n, present)} />);

    // The grid is bounded to (DSM_RENDER_BUDGET)² gridcells.
    expect(screen.getAllByRole("gridcell")).toHaveLength(
      DSM_RENDER_BUDGET * DSM_RENDER_BUDGET,
    );

    // The "showing top N of M" indicator is present with full M.
    expect(
      screen.getByText(
        new RegExp(`showing top ${DSM_RENDER_BUDGET} of ${n} services`, "i"),
      ),
    ).toBeInTheDocument();

    // The high-degree services (svc-10..svc-69) are kept; the zero-degree ones
    // (svc-0..svc-9) are dropped.
    expect(screen.getAllByText("svc-69").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("svc-10").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("svc-0")).not.toBeInTheDocument();
    expect(screen.queryByText("svc-9")).not.toBeInTheDocument();

    // The "services" summary reflects the FULL matrix (M), not the budget.
    expect(screen.getByText(String(n))).toBeInTheDocument();
  });
});
