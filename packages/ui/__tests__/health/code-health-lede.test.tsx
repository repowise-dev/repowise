import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { HealthOverviewSummary } from "@repowise-dev/types/health";
import { CodeHealthLede } from "../../src/health/code-health-lede.js";

function summary(partial: Partial<HealthOverviewSummary> = {}): HealthOverviewSummary {
  return {
    average_health: 7.0,
    maintainability_average: 8.0,
    hotspot_health: 4.5,
    performance_average: 9.7,
    performance_findings: 942,
    file_count: 3787,
    open_findings: 14755,
    structure_average: 1.59,
    history_average: 1.44,
    ...partial,
  } as HealthOverviewSummary;
}

describe("CodeHealthLede — how many scores the first screen carries", () => {
  // The page used to print Structure 8.4 beside Maintainability 8.0. Both are
  // code-shape readings on the same 0-10 scale, so the screen answered "which
  // number do I steer by?" twice, with two different numbers. The history half
  // is a share of the deduction now, which says the same thing without minting
  // a rival score.

  it("states the history share rather than a second score out of 10", () => {
    render(<CodeHealthLede summary={summary()} />);
    expect(screen.getByText("48%")).toBeTruthy();
    expect(screen.getByText(/comes from change history, not from the code/)).toBeTruthy();
  });

  it("no longer prints a structure figure", () => {
    const { container } = render(<CodeHealthLede summary={summary()} />);
    expect(container.textContent).not.toContain("Structure");
    expect(container.textContent).not.toContain("8.4");
  });

  it("carries one score out of 10, with Maintainability demoted to the ribbon", () => {
    const { container } = render(<CodeHealthLede summary={summary()} />);
    // The headline is the only figure at lede weight; Maintainability is still
    // on the page, as a ribbon stat beside the other cuts of the same scoring.
    expect(container.querySelectorAll("dt")).toHaveLength(4);
    expect(screen.getByText("Code health")).toBeTruthy();
    expect(screen.getByText("Maintainability")).toBeTruthy();
    expect(screen.getByText("8.0")).toBeTruthy();
    expect(screen.getAllByText("out of 10")).toHaveLength(1);
  });

  it("drops Open findings, which the tab row already counts", () => {
    render(<CodeHealthLede summary={summary()} />);
    expect(screen.queryByText("Open findings")).toBeNull();
  });

  it("gives every figure an explainer a reader can open", () => {
    render(<CodeHealthLede summary={summary()} />);
    for (const label of ["Code health", "Files", "Maintainability", "Performance risk", "Hotspot health"]) {
      expect(screen.getByLabelText(`What ${label} means`)).toBeTruthy();
    }
  });

  it("says nothing when the split was never recorded", () => {
    const { container } = render(
      <CodeHealthLede summary={summary({ structure_average: null, history_average: null })} />,
    );
    expect(container.textContent).not.toContain("comes from change history");
  });
});

describe("CodeHealthLede — the two readings of one figure", () => {
  it("names change history as the reason when it is counted", () => {
    render(<CodeHealthLede summary={summary({ counts: "everything" })} />);
    expect(screen.getByText(/comes from change history, not from the code/)).toBeTruthy();
  });

  it("says what it excluded, and that the findings now add up", () => {
    render(<CodeHealthLede summary={summary({ counts: "code_shape" })} />);
    expect(
      screen.getByText(/Change history excluded\. This scores the code alone/),
    ).toBeTruthy();
    expect(screen.queryByText(/comes from change history/)).toBeNull();
  });

  it("stops claiming churn and ownership are inputs once they are not", () => {
    const { container } = render(<CodeHealthLede summary={summary({ counts: "code_shape" })} />);
    expect(container.textContent).not.toContain("churn and ownership");
  });

  it("drops the bug-prediction claim, which only the calibrated score earns", () => {
    const accuracy = { hits: 20, k: 20, precision: 1, base_rate: 0.41, lift: 2.43, window_days: 180 };
    const withClaim = render(
      <CodeHealthLede summary={summary({ counts: "everything" })} accuracy={accuracy as never} />,
    );
    expect(withClaim.container.textContent).toContain("Ranked against real bug-fix history");
    withClaim.unmount();
    const withoutClaim = render(
      <CodeHealthLede summary={summary({ counts: "code_shape" })} accuracy={accuracy as never} />,
    );
    expect(withoutClaim.container.textContent).not.toContain("Ranked against real bug-fix history");
  });

  it("owns up to files it cannot score on this basis", () => {
    render(<CodeHealthLede summary={summary({ counts: "code_shape", unscored_files: 287 })} />);
    expect(screen.getByText(/287 files are not scored here/)).toBeTruthy();
  });
});
