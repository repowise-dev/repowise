import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ImpactEffortResponse } from "@repowise-dev/types/health";
import {
  ImpactEffortQuadrant,
  impactEffortQuadrantOf,
} from "../../src/health/impact-effort-quadrant.js";

function plane(partial: Partial<ImpactEffortResponse> = {}): ImpactEffortResponse {
  return {
    points: [
      { file_path: "a.py", effort_lines: 20, effort_basis: "plan", recoverable_health: 1.4, tier: "now", fix_rank: 1 },
      { file_path: "b.py", effort_lines: 900, effort_basis: "file", recoverable_health: 3.0, tier: null },
      { file_path: "c.py", effort_lines: 40, effort_basis: "file", recoverable_health: 0.2, tier: null },
      { file_path: "d.py", effort_lines: 300, effort_basis: "plan", recoverable_health: 0.1, tier: null },
    ],
    plotted: 4,
    total: 4,
    cap: 5000,
    effort_midline_lines: 150,
    gain_midline_points: 0.5,
    history_only_excluded: 7,
    ...partial,
  };
}

function svg(): SVGSVGElement {
  return document.querySelector("svg[viewBox]") as SVGSVGElement;
}

describe("ImpactEffortQuadrant", () => {
  it("places a file by the fixed midlines, not by the data", () => {
    expect(impactEffortQuadrantOf({ effort_lines: 20, recoverable_health: 1.4 }, 150, 0.5)).toBe("quick_wins");
    expect(impactEffortQuadrantOf({ effort_lines: 150, recoverable_health: 0.5 }, 150, 0.5)).toBe("major_projects");
    expect(impactEffortQuadrantOf({ effort_lines: 149, recoverable_health: 0.49 }, 150, 0.5)).toBe("minor_cleanups");
    expect(impactEffortQuadrantOf({ effort_lines: 2000, recoverable_health: 0 }, 150, 0.5)).toBe("time_sinks");
  });

  it("states coverage, quadrant names and the legend outside the drawing", () => {
    render(<ImpactEffortQuadrant data={plane()} />);
    expect(screen.getByText(/plotted of/).textContent).toMatch(/4 plotted of 4 files in this filter\./);
    expect(screen.getByText(/7 files with only history signals are not plotted/)).toBeInTheDocument();

    const quadrants = screen.getByLabelText("Quadrants");
    expect(within(quadrants).getByText("Quick wins").parentElement?.textContent).toMatch(/Quick wins 1 · under 150 lines, at least 0.5 points/);
    expect(within(quadrants).getByText("Time sinks").parentElement?.textContent).toMatch(/Time sinks 1 ·/);
    expect(screen.getByLabelText("Legend")).toBeInTheDocument();

    // None of that text lives inside the SVG: only axes, ticks and marks do.
    expect(svg().textContent).not.toMatch(/Quick wins|plotted|Selected/);
  });

  it("says when the server capped the set", () => {
    render(<ImpactEffortQuadrant data={plane({ plotted: 4, total: 6000, cap: 4 })} />);
    expect(screen.getByText(/plotted of/).textContent).toMatch(/capped at 4: the largest gains are kept/);
  });

  it("draws one mark per file with no colour ramp, a ring when no plan places it", () => {
    render(<ImpactEffortQuadrant data={plane()} />);
    const marks = screen.getByTestId("impact-effort-points").querySelectorAll("circle");
    expect(marks).toHaveLength(4);
    const plan = [...marks].find((c) => c.getAttribute("data-file") === "d.py")!;
    const file = [...marks].find((c) => c.getAttribute("data-file") === "b.py")!;
    expect(plan.getAttribute("class")).toContain("fill-[var(--color-text-tertiary)]");
    expect(file.getAttribute("class")).toContain("fill-transparent");
    for (const c of marks) expect(c.getAttribute("class")).not.toContain("accent");
  });

  it("finds a clicked file and describes the hovered one", () => {
    const onSelect = vi.fn();
    render(<ImpactEffortQuadrant data={plane()} onSelect={onSelect} />);
    const mark = document.querySelector('[data-file="b.py"]')!;

    fireEvent.pointerOver(mark);
    expect(screen.getByText("b.py")).toBeInTheDocument();
    expect(screen.getByText(/No plan recovers health here: 900 code lines, findings deduct 3.00 points/)).toBeInTheDocument();

    fireEvent.click(mark);
    expect(onSelect).toHaveBeenCalledWith("b.py");
  });

  it("marks the selection in the accent colour, outside the memoised field", () => {
    render(<ImpactEffortQuadrant data={plane()} selectedPath="c.py" />);
    const accent = [...svg().querySelectorAll("circle")].filter((c) =>
      (c.getAttribute("class") ?? "").includes("accent-primary"),
    );
    expect(accent).toHaveLength(1);
    expect(accent[0]!.closest("[data-testid=impact-effort-points]")).toBeNull();
  });

  it("walks the points from the keyboard, largest gain first as served", () => {
    const onSelect = vi.fn();
    render(<ImpactEffortQuadrant data={plane()} onSelect={onSelect} />);
    const plot = screen.getByRole("group", { name: /Impact and effort plot of 4 files/ });

    fireEvent.keyDown(plot, { key: "ArrowRight" });
    expect(screen.getByText("a.py")).toBeInTheDocument();
    expect(screen.getByText(/Plan changes 20 lines and recovers 1.40 points · Fix first #1, Now · Quick wins/)).toBeInTheDocument();

    fireEvent.keyDown(plot, { key: "End" });
    fireEvent.keyDown(plot, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("d.py");
  });

  it("numbers Fix-first items in ink over a lighter field, with no orange", () => {
    render(<ImpactEffortQuadrant data={plane()} />);
    const ranked = document.querySelector('[data-rank="1"]')!;
    expect(ranked.textContent).toBe("1");
    const ring = ranked.querySelector("circle")!;
    expect(ring.getAttribute("data-file")).toBe("a.py");
    expect(ring.getAttribute("class")).toContain("stroke-[var(--color-text-primary)]");
    expect(ranked.innerHTML).not.toContain("accent");
    const other = document.querySelector('[data-file="d.py"]')!;
    expect(Number(other.getAttribute("fill-opacity"))).toBeLessThan(0.5);
    expect(screen.getByLabelText("Legend").textContent).toMatch(/numbered by its place in Fix first/);
  });

  it("keeps marks clear of the y-axis tick labels", () => {
    render(<ImpactEffortQuadrant data={plane()} />);
    const leftmost = Math.min(
      ...[...screen.getByTestId("impact-effort-points").querySelectorAll("circle")].map((c) =>
        Number(c.getAttribute("cx")),
      ),
    );
    const yAxis = Number(svg().querySelectorAll("line")[1]!.getAttribute("x1"));
    // A rank marker has radius 7; the inset keeps it off the axis and its labels.
    expect(leftmost - 7).toBeGreaterThan(yAxis);
  });
});
