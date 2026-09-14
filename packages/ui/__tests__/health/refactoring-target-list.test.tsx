import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  RefactoringTargetList,
  type RefactoringTarget,
} from "../../src/health/refactoring-target-list.js";

const CARD_PAGE = 50;

function targets(n: number): RefactoringTarget[] {
  return Array.from({ length: n }, (_, i) => ({
    file_path: `src/f${String(i).padStart(3, "0")}.py`,
    score: 1.0,
    nloc: 900 - i,
    primary_biomarker: "brain_method",
    primary_severity: "high" as const,
    primary_reason: "Oversized, deeply-nested function.",
    primary_function: "_run",
    primary_line_start: 10,
    primary_line_end: 400,
    total_impact: 6.2,
    finding_count: 3,
    biomarkers: ["brain_method"],
    effort_bucket: "XL" as const,
    impact_per_effort: 1.24,
  }));
}

function renderedPaths(): string[] {
  return Array.from(document.querySelectorAll("[data-health-work-item]")).map(
    (el) => el.getAttribute("data-health-work-item") ?? "",
  );
}

describe("RefactoringTargetList paging", () => {
  it("mounts a page of cards, not the whole queue", () => {
    // The queue's own "Load more" raises the fetch to QUEUE_MAX = 500, and
    // every one of those used to be mounted as a full card in one commit.
    render(<RefactoringTargetList targets={targets(500)} />);
    expect(renderedPaths()).toHaveLength(CARD_PAGE);
    expect(renderedPaths()[0]).toBe("src/f000.py");
  });

  it("says how many are left and reveals another page per click", () => {
    render(<RefactoringTargetList targets={targets(120)} />);
    expect(screen.getByRole("button", { name: /Show 50 more \(70 remaining\)/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Show 50 more/ }));
    expect(renderedPaths()).toHaveLength(100);

    // The last page is short, and the control says so rather than over-promising.
    fireEvent.click(screen.getByRole("button", { name: /Show 20 more \(20 remaining\)/ }));
    expect(renderedPaths()).toHaveLength(120);
    expect(screen.queryByRole("button", { name: /Show .* more/ })).not.toBeInTheDocument();
  });

  it("shows a short list whole, with no control", () => {
    render(<RefactoringTargetList targets={targets(3)} />);
    expect(renderedPaths()).toHaveLength(3);
    expect(screen.queryByRole("button", { name: /remaining/ })).not.toBeInTheDocument();
  });

  it("opens enough pages to reach a highlighted target past the window", () => {
    // The quadrant highlights by file path and the card carries the only DOM
    // anchor for it, so a click on a deep dot would otherwise scroll to a node
    // that was never mounted — a silent no-op, and the failure a naive cap
    // introduces.
    render(<RefactoringTargetList targets={targets(300)} highlightedPath="src/f210.py" />);
    expect(renderedPaths()).toContain("src/f210.py");
    // Rounded up to a whole page, not "everything up to it".
    expect(renderedPaths()).toHaveLength(250);
  });

  it("returns to the first page when the list changes", () => {
    // Re-filtering is a new question; leaving the reader three pages deep in
    // results they have not seen is worse than the scroll they lose.
    const { rerender } = render(<RefactoringTargetList targets={targets(300)} />);
    fireEvent.click(screen.getByRole("button", { name: /Show 50 more/ }));
    expect(renderedPaths()).toHaveLength(100);

    rerender(<RefactoringTargetList targets={targets(300).slice(10)} />);
    expect(renderedPaths()).toHaveLength(CARD_PAGE);
  });

  it("still shows the empty state", () => {
    render(<RefactoringTargetList targets={[]} emptyMessage="Nothing here." />);
    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
  });
});
