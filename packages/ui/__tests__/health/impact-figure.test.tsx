/**
 * How a deduction is printed, in one place.
 *
 * Two zeros meet here and must not be confused. A performance finding scores
 * exactly zero by construction and has no deduction to show; a defect finding
 * worth a thousandth of a point has one that is merely too small to print. The
 * old formatter rendered both as a red "-0.00".
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ImpactFigure } from "../../src/health/impact-figure.js";
import { formatDelta, formatHealthImpact } from "../../src/health/tokens.js";

describe("formatHealthImpact", () => {
  it("has nothing to print for a finding that scores no impact", () => {
    expect(formatHealthImpact(0)).toBeNull();
    expect(formatHealthImpact(null)).toBeNull();
    expect(formatHealthImpact(undefined)).toBeNull();
  });

  it("prints a real deduction too small to show as a bound, not as zero", () => {
    // The alternative reads as "measured, and it costs nothing", which is a
    // stronger claim than the number supports.
    expect(formatHealthImpact(0.003)).toBe("−<0.01");
    expect(formatHealthImpact(-0.003)).toBe("−<0.01");
  });

  it("prints an ordinary deduction to two places", () => {
    expect(formatHealthImpact(1.234)).toBe("−1.23");
    expect(formatHealthImpact(2.5)).toBe("−2.50");
  });

  it("never yields a signed zero", () => {
    for (const v of [0, -0, 0.0001, -0.0001, 0.004]) {
      expect(formatHealthImpact(v)).not.toBe("−0.00");
    }
  });
});

describe("formatDelta", () => {
  it("does not sign a delta that rounded to nothing", () => {
    expect(formatDelta(-0.001)).toBe("0.00");
    expect(formatDelta(0.001)).toBe("0.00");
    expect(formatDelta(-0.4)).toBe("-0.40");
    expect(formatDelta(0.4)).toBe("+0.40");
  });
});

describe("ImpactFigure", () => {
  it("says a marker is unscored in a word, not a number", () => {
    const { container } = render(<ImpactFigure impact={0} />);
    expect(container.textContent).toBe("not scored");
  });

  it("keeps the deduction colour for deductions only", () => {
    // Red is what a deduction looks like on this surface. A marker with none
    // wearing it is the same confusion the figure was meant to end.
    const unscored = render(<ImpactFigure impact={0} />).container.firstElementChild;
    expect(unscored?.className).not.toContain("color-error");

    const scored = render(<ImpactFigure impact={1.2} />).container.firstElementChild;
    expect(scored?.className).toContain("color-error");
    expect(scored?.textContent).toBe("−1.20");
  });

  it("keeps the caller's layout classes on both branches", () => {
    for (const impact of [0, 1.2]) {
      const el = render(<ImpactFigure impact={impact} className="ml-auto text-xs" />)
        .container.firstElementChild;
      expect(el?.className).toContain("ml-auto");
    }
  });
});
