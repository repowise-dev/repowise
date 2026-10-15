import { describe, it, expect } from "vitest";
import { createEvent, fireEvent, render, screen } from "@testing-library/react";
import { TrendChart, type TrendSeriesPoint } from "../../src/health/trend-chart.js";

const history: TrendSeriesPoint[] = [
  { taken_at: "2026-10-01T10:00:00Z", average_health: 3.2, hotspot_health: 2.5, worst_performer_score: 1, history_average: 1.4 },
  { taken_at: "2026-10-02T10:00:00Z", average_health: 3.6, hotspot_health: 2.4, worst_performer_score: 1, history_average: 1.2 },
  { taken_at: "2026-10-03T10:00:00Z", average_health: 3.5, hotspot_health: 2.9, worst_performer_score: 1.5, history_average: 1.1 },
];

/**
 * React reads a pointer leave off the `pointerout` it pairs with, and jsdom has
 * no PointerEvent to carry `pointerType`, so the field is set by hand.
 */
function leave(el: Element, pointerType: string) {
  const event = createEvent.pointerOut(el, { relatedTarget: document.body });
  Object.defineProperty(event, "pointerType", { value: pointerType });
  fireEvent(el, event);
}

const live = (container: HTMLElement) => container.querySelector("[aria-live]")!.textContent ?? "";

describe("TrendChart", () => {
  it("names the health bands behind the lines instead of colouring the lines by health", () => {
    const { container } = render(<TrendChart history={history} />);
    for (const band of ["Healthy", "Warning", "Alert"]) expect(screen.getByText(band)).toBeInTheDocument();
    const strokes = [...container.querySelectorAll("path")].map((p) => p.getAttribute("stroke"));
    // A code-health line at 3 is not drawn in the Alert red or any verdict colour.
    expect(strokes).toContain("var(--color-series-1)");
    expect(strokes.some((s) => /success|warning|error/.test(s ?? ""))).toBe(false);
  });

  it("steps through snapshots from the keyboard and reads each one out", () => {
    const { container } = render(<TrendChart history={history} />);
    const plot = screen.getByRole("group", { name: /Arrow keys step through snapshots/ });

    fireEvent.focus(plot);
    expect(live(container)).toMatch(/snapshot 3 of 3/);
    expect(live(container)).toMatch(/Code health 3\.5, -0\.10/);

    fireEvent.keyDown(plot, { key: "ArrowLeft" });
    expect(live(container)).toMatch(/snapshot 2 of 3/);
    expect(live(container)).toMatch(/Code health 3\.6, \+0\.40/);
    expect(live(container)).toMatch(/Change history takes 1\.20 points off code health\./);

    fireEvent.keyDown(plot, { key: "Home" });
    // The first snapshot has nothing before it, so it carries no delta.
    expect(live(container)).toMatch(/Code health 3\.2;/);
  });

  it("toggles a series from its legend entry and never hides the last one", () => {
    render(<TrendChart history={history} />);
    const hotspot = screen.getByRole("button", { name: "Hotspot" });
    fireEvent.click(hotspot);
    expect(hotspot).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "Worst file" }));
    expect(screen.getByRole("button", { name: "Code health" })).toBeDisabled();
    fireEvent.click(hotspot);
    expect(screen.getByRole("button", { name: "Code health" })).not.toBeDisabled();
  });

  it("leaves a hidden series out of the readout", () => {
    const { container } = render(<TrendChart history={history} />);
    fireEvent.click(screen.getByRole("button", { name: "Hotspot" }));
    fireEvent.focus(screen.getByRole("group", { name: /Arrow keys/ }));
    expect(live(container)).not.toMatch(/Hotspot/);
    expect(live(container)).toMatch(/Worst file 1\.5/);
  });
  it("keeps a tapped readout open after the touch ends, and closes it on a press elsewhere", () => {
    render(
      <div>
        <TrendChart history={history} />
        <button type="button">elsewhere</button>
      </div>,
    );
    const plot = screen.getByRole("group", { name: /Arrow keys/ });
    const svg = plot.querySelector("svg")!;
    fireEvent.focus(plot);
    expect(screen.getByText("3 of 3")).toBeInTheDocument();
    // Touch fires pointerleave straight after pointerup.
    leave(svg, "touch");
    expect(screen.getByText("3 of 3")).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByRole("button", { name: "elsewhere" }));
    expect(screen.queryByText("3 of 3")).toBeNull();
    // A mouse leaving the plot still clears it.
    fireEvent.focus(plot);
    leave(svg, "mouse");
    expect(screen.queryByText("3 of 3")).toBeNull();
  });

  it("draws every series again when a new history drops the only one left on", () => {
    const withPillar = history.map((p) => ({ ...p, maintainability_average: 8.2 }));
    const { container, rerender } = render(<TrendChart history={withPillar} />);
    for (const name of ["Code health", "Hotspot", "Worst file"]) {
      fireEvent.click(screen.getByRole("button", { name }));
    }
    expect(screen.getByRole("button", { name: "Maintainability" })).toBeDisabled();

    rerender(<TrendChart history={history} />);

    const strokes = [...container.querySelectorAll("path")].map((p) => p.getAttribute("stroke"));
    expect(strokes).toContain("var(--color-series-1)");
    const codeHealth = screen.getByRole("button", { name: "Code health" });
    expect(codeHealth).toHaveAttribute("aria-pressed", "true");
    expect(codeHealth).not.toBeDisabled();
    // Hiding one from the fallback hides that one, not the others.
    fireEvent.click(screen.getByRole("button", { name: "Hotspot" }));
    expect(screen.getByRole("button", { name: "Hotspot" })).toHaveAttribute("aria-pressed", "false");
    expect(codeHealth).toHaveAttribute("aria-pressed", "true");
  });
});
