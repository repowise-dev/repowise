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

describe("RefactoringCard lower priority", () => {
  it("labels a demoted finding and leaves the others unlabelled", () => {
    const [a, b] = findings();
    render(
      <RefactoringCard
        target={target({
          all_findings: [{ ...a!, lower_priority: "lower priority: near the bar" }, b!],
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Show all 3 findings/ }));
    expect(screen.getAllByText(/Lower priority: near the bar/)).toHaveLength(1);
  });
});

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

describe("a file led by a history marker", () => {
  it("reads as Watch, explains why, and offers no fix prompt", () => {
    render(
      <RefactoringCard
        target={target({ primary_biomarker: "change_entropy", biomarkers: ["change_entropy"] })}
        onGeneratePrompt={vi.fn()}
      />,
    );
    expect(screen.getByText("Watch")).toBeInTheDocument();
    expect(screen.getByText(/Editing the file will not clear it/)).toBeInTheDocument();
    expect(screen.queryByText("AI fix prompt")).not.toBeInTheDocument();
  });

  it("keeps the fix prompt for a code-shape lead", () => {
    render(<RefactoringCard target={target()} onGeneratePrompt={vi.fn()} />);
    expect(screen.getByText("AI fix prompt")).toBeInTheDocument();
    expect(screen.queryByText("Watch")).not.toBeInTheDocument();
  });
});

describe("the row's labels and action", () => {
  it("labels a test file, and only a test file", () => {
    const { rerender } = render(<RefactoringCard target={target({ is_test: true })} />);
    expect(screen.getByText("test")).toBeInTheDocument();
    rerender(<RefactoringCard target={target({ is_test: false })} />);
    expect(screen.queryByText("test")).not.toBeInTheDocument();
  });

  it("shows the core action sentence as the row's action line", () => {
    render(
      <RefactoringCard target={target({ primary_suggestion: "Split this function." })} />,
    );
    expect(screen.getByText("Action").parentElement?.textContent).toBe(
      "ActionSplit this function.",
    );
  });

  it("offers a checkbox for the finding the row names", () => {
    const toggle = vi.fn();
    render(
      <RefactoringCard
        target={target({ primary_finding_id: "f1" })}
        onToggleSelect={toggle}
        selected
      />,
    );
    const box = screen.getByRole("checkbox", {
      name: /Select the .* finding in packages\/core\/pipeline\/incremental.py/,
    });
    expect(box).toBeChecked();
    fireEvent.click(box);
    expect(toggle).toHaveBeenCalledWith(expect.objectContaining({ primary_finding_id: "f1" }));
  });

  it("offers no checkbox when the row names no finding", () => {
    render(<RefactoringCard target={target()} onToggleSelect={vi.fn()} />);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
