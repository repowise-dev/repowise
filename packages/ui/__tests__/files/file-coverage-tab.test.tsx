import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FileCoverageTab } from "../../src/files/file-coverage-tab.js";
import type { FileDetailCoverage } from "@repowise-dev/types/files";

const HTML = '<pre><code><span class="line" data-covered="y" data-line="1">x = 1</span></code></pre>';

function makeCoverage(overrides: Partial<FileDetailCoverage> = {}): FileDetailCoverage {
  return {
    line_coverage_pct: 75,
    branch_coverage_pct: null,
    total_coverable_lines: 4,
    covered_line_count: 3,
    covered_lines: [1, 2, 3],
    source_format: "lcov",
    ingested_at: null,
    ingested_commit_sha: "abcdef1234567",
    ...overrides,
  };
}

describe("FileCoverageTab", () => {
  it("states measured coverage as lines the tests ran, without claiming exactness", () => {
    const { container } = render(<FileCoverageTab coverage={makeCoverage()} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Tests ran");
    expect(text).toContain("that no test executed");
    expect(text).not.toContain("Tests reach");
    expect(text).not.toContain("exact");
  });

  it("tints the source when the coverage is current", () => {
    const { container } = render(
      <FileCoverageTab coverage={makeCoverage({ freshness: "current" })} coverageCodeHtml={HTML} />,
    );
    expect(container.querySelector('[data-covered="y"]')).not.toBeNull();
    expect(screen.queryByText(/changed after coverage was measured/)).toBeNull();
  });

  it("shows no tint and says why when the file changed after the run", () => {
    const { container } = render(
      <FileCoverageTab coverage={makeCoverage({ freshness: "stale" })} coverageCodeHtml={HTML} />,
    );
    expect(container.querySelector("[data-covered]")).toBeNull();
    expect(
      screen.getByText(
        "This file changed after coverage was measured; re-run tests with coverage to refresh.",
      ),
    ).toBeTruthy();
  });
});
