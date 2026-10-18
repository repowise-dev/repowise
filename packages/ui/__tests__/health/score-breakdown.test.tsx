import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScoreBreakdown } from "../../src/health/score-breakdown.js";

describe("ScoreBreakdown arithmetic", () => {
  it("states the sum plainly above the floor", () => {
    render(<ScoreBreakdown score={7.5} totalDeduction={2.5} categories={[]} />);
    expect(screen.getByText("10.0 − 2.50 = 7.5")).toBeInTheDocument();
  });

  it("names the floor when the deductions go past it", () => {
    render(<ScoreBreakdown score={1.0} totalDeduction={11.2} categories={[]} />);
    expect(screen.getByText("10.0 − 11.20 = −1.20, floored at 1.0")).toBeInTheDocument();
  });
});
