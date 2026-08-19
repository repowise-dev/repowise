import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  RefactoringCard,
  type RefactoringTarget,
  type RefactoringTargetFinding,
} from "../../src/health/refactoring-card.js";

function target(partial: Partial<RefactoringTarget> = {}): RefactoringTarget {
  return {
    file_path: "packages/core/pipeline/incremental.py",
    score: 1.0,
    nloc: 900,
    primary_biomarker: "brain_method",
    primary_severity: "high",
    primary_reason: "Oversized, deeply-nested function.",
    primary_function: "_run",
    primary_line_start: 10,
    primary_line_end: 400,
    total_impact: 6.2,
    finding_count: 3,
    biomarkers: ["brain_method"],
    effort_bucket: "XL",
    impact_per_effort: 1.24,
    ...partial,
  };
}

function findings(): RefactoringTargetFinding[] {
  return [
    {
      id: "a",
      biomarker_type: "brain_method",
      severity: "high",
      function_name: "_run",
      health_impact: 3.2,
      reason: "Oversized, deeply-nested function.",
    },
    {
      id: "b",
      biomarker_type: "error_handling",
      severity: "low",
      function_name: null,
      health_impact: 0.15,
      reason: "broad `except Exception` catches unrelated errors.",
    },
  ];
}

describe("RefactoringCard lazy findings", () => {
  it("offers the expander from finding_count, without the findings themselves", () => {
    // The list response no longer ships `all_findings`; the expander must not
    // disappear just because the payload got smaller.
    render(<RefactoringCard target={target()} onLoadFindings={async () => []} />);
    expect(screen.getByRole("button", { name: /Show all 3 findings/ })).toBeInTheDocument();
  });

  it("fetches findings on first expand and reuses them on re-expand", async () => {
    const load = vi.fn(async () => findings());
    render(<RefactoringCard target={target()} onLoadFindings={load} />);

    expect(load).not.toHaveBeenCalled(); // nothing fetched until the click

    fireEvent.click(screen.getByRole("button", { name: /Show all 3 findings/ }));
    await waitFor(() =>
      expect(screen.getByText(/broad `except Exception`/)).toBeInTheDocument(),
    );
    expect(load).toHaveBeenCalledWith("packages/core/pipeline/incremental.py");

    // Collapse and re-expand: cached, so no second request.
    fireEvent.click(screen.getByRole("button", { name: /Hide all 3 findings/ }));
    fireEvent.click(screen.getByRole("button", { name: /Show all 3 findings/ }));
    await waitFor(() =>
      expect(screen.getByText(/broad `except Exception`/)).toBeInTheDocument(),
    );
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("keeps the card usable when the findings fetch fails", async () => {
    const load = vi.fn(async () => {
      throw new Error("boom");
    });
    render(<RefactoringCard target={target()} onLoadFindings={load} />);
    fireEvent.click(screen.getByRole("button", { name: /Show all 3 findings/ }));
    await waitFor(() =>
      expect(screen.getByText("Could not load findings.")).toBeInTheDocument(),
    );
    // The header still carries the primary finding.
    expect(screen.getByText(/Oversized, deeply-nested function/)).toBeInTheDocument();
  });

  it("prefers findings already on the target over a fetch", async () => {
    const load = vi.fn(async () => []);
    render(
      <RefactoringCard
        target={target({ all_findings: findings() })}
        onLoadFindings={load}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Show all 3 findings/ }));
    await waitFor(() =>
      expect(screen.getByText(/broad `except Exception`/)).toBeInTheDocument(),
    );
    expect(load).not.toHaveBeenCalled();
  });

  it("hides the expander when the file has no findings", () => {
    render(
      <RefactoringCard target={target({ finding_count: 0 })} onLoadFindings={async () => []} />,
    );
    expect(screen.queryByRole("button", { name: /findings/ })).not.toBeInTheDocument();
  });
});
