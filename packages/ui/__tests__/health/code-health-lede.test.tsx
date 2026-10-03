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
    expect(screen.getByText("Change history excluded.")).toBeTruthy();
    // The rest of the caption waits behind Breakdown.
    expect(screen.getByText(/This scores the code alone/).closest("details")).toBeTruthy();
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

describe("CodeHealthLede — the figure and its band agree at the edge", () => {
  it("does not print 7.0 beside Fair for a score just under Good", () => {
    const { container } = render(<CodeHealthLede summary={summary({ average_health: 6.98 })} />);
    expect(screen.getByText("6.9")).toBeTruthy();
    expect(screen.getByText("Fair")).toBeTruthy();
    expect(container.textContent).not.toContain("7.0");
  });
});

describe("CodeHealthLede: what the first screen shows", () => {
  const accuracy = { hits: 17, k: 20, precision: 0.85, base_rate: 0.1, lift: 8.61, window_days: 180 };

  it("shows the validation line with its facts, outside any disclosure", () => {
    render(<CodeHealthLede summary={summary()} accuracy={accuracy as never} />);
    const line = screen.getByText(/Ranked against real bug-fix history/);
    expect(line.textContent).toContain("17 of the 20 files");
    expect(line.textContent).toContain("85% against a 10% base rate");
    expect(line.textContent).toContain("8.61× better");
    expect(line.closest("details")).toBeNull();
  });

  it("keeps the band breakdown and the history share behind a closed Breakdown", () => {
    const distribution = {
      total_files: 2,
      total_nloc: 100,
      bands: {
        at_risk: { pct: 50, files: 1, nloc: 50 },
        excellent: { pct: 50, files: 1, nloc: 50 },
      },
    };
    render(<CodeHealthLede summary={summary()} distribution={distribution as never} />);
    const toggle = screen.getByText("Breakdown");
    const details = toggle.closest("details")!;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("50% at risk");
    expect(details.textContent).toContain("comes from change history");
  });

  it("puts the method, performance and test-file notes behind How it is scored", () => {
    render(
      <CodeHealthLede
        summary={summary({ worst_test_path: "tests/test_big.py", worst_test_score: 2.4 })}
      />,
    );
    const details = screen.getByText("How it is scored").closest("details")!;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("churn and ownership");
    expect(details.textContent).toContain("Static performance is scored separately");
    expect(details.textContent).toContain("Test files are ranked apart.");
  });
});

describe("CodeHealthLede: the worst test file", () => {
  it("names the lowest-scoring test file apart from production", () => {
    const { container } = render(
      <CodeHealthLede
        summary={summary({ worst_test_path: "tests/test_big.py", worst_test_score: 2.4 })}
      />,
    );
    expect(screen.getByText("tests/test_big.py")).toBeInTheDocument();
    expect(container.textContent).toContain("Test files are ranked apart.");
    expect(container.textContent).toContain("at 2.4.");
  });

  it("says nothing about tests when there are none, or the server predates it", () => {
    const { container } = render(<CodeHealthLede summary={summary({ worst_test_path: null })} />);
    expect(container.textContent).not.toContain("Test files are ranked apart");
  });
});

describe("CodeHealthLede secondary, when Fix first leads the page", () => {
  it("keeps one line with the score and its band, and the ribbon behind More", () => {
    const { container } = render(<CodeHealthLede summary={summary()} variant="secondary" />);
    const summaryLine = container.querySelector("summary")!;
    expect(summaryLine.textContent).toContain("7.0");
    expect(summaryLine.textContent).toContain("out of 10 across 3,787 files");
    expect(summaryLine.textContent).toContain("More");
    // The other figures still exist, inside the closed disclosure.
    const details = container.querySelector("details")!;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("Maintainability");
    expect(details.textContent).toContain("Hotspot health");
  });
});
