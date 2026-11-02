import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { HealthFileDrawer } from "../../src/health/health-file-drawer.js";
import { scoreSplit, type SplitCategory } from "../../src/health/score-split.js";
import { FilePageHeader } from "../../src/files/file-page-header.js";
import type { FileDetailResponse } from "@repowise-dev/types/files";
import { drawerMetric, drawerSignals } from "./drawer-test-utils.js";

function category(name: string, applied: number): SplitCategory {
  return { category: name, applied_deduction: applied };
}

const CATEGORIES = [
  category("structural_complexity", 1.2),
  category("size_and_complexity", 0.5),
  category("organizational", 0.8),
  category("test_coverage", 0.3),
  category("test_coverage_gradient", 0.2),
];

function breakdown(categories = CATEGORIES) {
  const total = categories.reduce((s, c) => s + c.applied_deduction, 0);
  return {
    score: 10 - total,
    total_deduction: total,
    categories: categories.map((c) => ({
      ...c,
      cap: 2,
      raw_deduction: c.applied_deduction,
      capped: false,
      finding_count: 1,
      findings: [],
    })),
  };
}

describe("scoreSplit", () => {
  it("puts history in its own part, coverage in its own, and the rest in code shape", () => {
    const split = scoreSplit(CATEGORIES);
    expect(split.shape).toBeCloseTo(1.7);
    expect(split.history).toBeCloseTo(0.8);
    expect(split.coverage).toBeCloseTo(0.5);
  });

  it("counts a category it does not know as code shape", () => {
    expect(scoreSplit([category("new_kind", 0.4)]).shape).toBeCloseTo(0.4);
  });
});

describe("HealthFileDrawer score split", () => {
  it("shows the three parts of the deduction beside the score", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={drawerMetric({ score: 7.0 })}
        breakdown={breakdown()}
      />,
    );
    const split = screen.getByRole("group", { name: "What the score lost" });
    expect(split).toHaveTextContent("Code shape");
    expect(split).toHaveTextContent("−1.70");
    expect(split).toHaveTextContent("Change history");
    expect(split).toHaveTextContent("−0.80");
    expect(split).toHaveTextContent("Test coverage");
    expect(split).toHaveTextContent("−0.50");
  });

  it("says when the file's history is too short to count", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={drawerMetric({ score: 7.0 })}
        breakdown={breakdown()}
        signals={drawerSignals({ history: "short" })}
      />,
    );
    expect(screen.getByText(/fewer than 5 commits/)).toBeInTheDocument();
  });

  it("renders nothing without a breakdown or a short history", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={drawerMetric({ score: 7.0 })} />);
    expect(screen.queryByRole("group", { name: "What the score lost" })).not.toBeInTheDocument();
  });

  it("shows a part that lost nothing without a minus sign", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={drawerMetric({ score: 8.8 })}
        breakdown={breakdown([category("structural_complexity", 1.2)])}
      />,
    );
    const split = screen.getByRole("group", { name: "What the score lost" });
    expect(split).toHaveTextContent("Change history0.00");
  });

  it("stays quiet about history when it is long enough", () => {
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={drawerMetric({ score: 7.0 })}
        breakdown={breakdown()}
        signals={drawerSignals()}
      />,
    );
    expect(screen.queryByText(/fewer than 5 commits/)).not.toBeInTheDocument();
  });
});

describe("FilePageHeader score split", () => {
  it("shows the split and the short-history note under the score", () => {
    const data = {
      file_path: "src/a.py",
      wiki_page: null,
      health: {
        metric: { ...drawerMetric({ score: 7.0 }), line_coverage_pct: null, duplication_pct: null },
        breakdown: breakdown(),
        findings: [],
        trend: null,
        signals: drawerSignals({ history: "short" }),
      },
      git: null,
      coverage: null,
      graph: null,
      symbols: [],
      function_blame: [],
      governing_decisions: [],
      dead_code: [],
    } as unknown as FileDetailResponse;
    render(<FilePageHeader data={data} linkPrefix="/repos/r1" />);
    const split = screen.getByRole("group", { name: "What the score lost" });
    expect(split).toHaveTextContent("−1.70");
    expect(screen.getByText(/fewer than 5 commits/)).toBeInTheDocument();
  });
});
