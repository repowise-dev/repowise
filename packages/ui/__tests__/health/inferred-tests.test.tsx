/**
 * The Tests tab on the inferred basis.
 *
 * These are mostly *negative* assertions, and deliberately so. The graph-inferred
 * test map over-claims by construction and carries no line attribution, so the
 * failure mode this tab has to be protected from is not "renders wrong" but
 * "renders convincingly as a measurement": a percentage, a progress bar, or a
 * health-band colour on data that has no band. Each of those is one convenience
 * change away at any time, so each is asserted directly.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type {
  HealthCoverageResponse,
  InferredTestMap,
  ReachedFileRow,
  TestsReachingFile,
} from "@repowise-dev/types/health";

import { RiskCoverageScatter } from "../../src/health/risk-coverage-scatter.js";
import { InferredTestsView } from "../../src/health/inferred-tests-view.js";
import { TestsReachingList } from "../../src/health/tests-reaching-list.js";

function row(
  partial: Partial<ReachedFileRow> & { file_path: string },
): ReachedFileRow {
  return { reached: false, health_score: 5, nloc: 100, ...partial };
}

function response(map: Partial<InferredTestMap> = {}): HealthCoverageResponse {
  const files = map.files ?? [
    row({ file_path: "src/a.py", reached: true, health_score: 8 }),
    row({ file_path: "src/b.py", reached: false, health_score: 3 }),
  ];
  return {
    basis: "inferred",
    summary: {
      file_count: 0,
      covered_lines: 0,
      total_lines: 0,
      line_coverage_pct: null,
      branch_coverage_pct: null,
      source_format: null,
      ingested_at: null,
      ingested_commit_sha: null,
    },
    files: [],
    modules: [],
    modules_total: 0,
    inferred: {
      files,
      files_total: files.length,
      files_reached: files.filter((f) => f.reached).length,
      files_not_reached: files.filter((f) => !f.reached).length,
      test_file_count: 12,
      ...map,
    },
  };
}

describe("RiskCoverageScatter, inferred basis", () => {
  const points = [
    { file_path: "src/a.py", health_score: 8, line_coverage_pct: null, nloc: 50, reached: true },
    { file_path: "src/b.py", health_score: 3, line_coverage_pct: null, nloc: 90, reached: false },
  ];

  it("names the two columns instead of a percentage axis", () => {
    render(<RiskCoverageScatter basis="inferred" points={points} />);

    expect(screen.getByText("no test reaches it")).toBeInTheDocument();
    expect(screen.getByText("a test reaches it")).toBeInTheDocument();
    // The measured axis ticks must not survive the collapse: a "%" anywhere on
    // this chart is the claim the basis is not allowed to make.
    expect(screen.queryByText("50%")).not.toBeInTheDocument();
    expect(screen.queryByText("100%")).not.toBeInTheDocument();
  });

  it("keeps the measured axis when no basis is passed", () => {
    render(<RiskCoverageScatter points={[{ ...points[0]!, line_coverage_pct: 80 }]} />);

    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.queryByText("a test reaches it")).not.toBeInTheDocument();
  });

  it("paints the dots by column in the sunset pair, never the health ramp", () => {
    // Health is already the Y axis, so a health-banded dot would re-say its own
    // position and leave nothing carrying the split. It would also paint a
    // static reading in the colours reserved for measured health bands.
    const { container } = render(
      <RiskCoverageScatter basis="inferred" points={points} />,
    );
    const fillOf = (path: string) =>
      container.querySelector(`circle[data-file="${path}"]`)?.getAttribute("fill");

    expect(fillOf("src/a.py")).toBe("var(--color-accent-fill)");
    expect(fillOf("src/b.py")).toBe("var(--color-accent-secondary)");
    // src/b.py scores 3, which on the measured chart would be the error band.
    expect(container.innerHTML).not.toContain("--color-error");
    expect(container.innerHTML).not.toContain("--color-success");
  });

  it("separates the columns and keeps each file's position stable across renders", () => {
    // The jitter is seeded off the path precisely so a re-render does not move
    // the field. Random jitter would pass a single-render test and fail a user.
    const { container, rerender } = render(
      <RiskCoverageScatter basis="inferred" points={points} />,
    );
    const cxOf = (path: string) =>
      Number(
        container.querySelector(`circle[data-file="${path}"]`)?.getAttribute("cx"),
      );

    const firstA = cxOf("src/a.py");
    const firstB = cxOf("src/b.py");
    expect(firstA).toBeGreaterThan(firstB);

    rerender(<RiskCoverageScatter basis="inferred" points={[...points]} />);

    expect(cxOf("src/a.py")).toBe(firstA);
    expect(cxOf("src/b.py")).toBe(firstB);
  });
});

describe("InferredTestsView", () => {
  it("leads with the count of risky files nothing reaches", () => {
    render(
      <InferredTestsView
        data={response()}
        untestedFindings={[]}
        onOpenFile={vi.fn()}
      />,
    );

    expect(screen.getByText("Files nothing tests")).toBeInTheDocument();
    // src/b.py alone: unreached and under the at-risk score. src/a.py is
    // reached, and an unreached file scoring well is not the headline. The
    // singular unit is the assertion — it can only render from a count of one.
    expect(screen.getByText("risky file")).toBeInTheDocument();
  });

  it("renders no percentage and no progress bar anywhere", () => {
    const { container } = render(
      <InferredTestsView
        data={response()}
        untestedFindings={[]}
        onOpenFile={vi.fn()}
      />,
    );

    expect(container.textContent).not.toMatch(/\d+(\.\d+)?%/);
    // A bar is a percentage drawn. The measured tab renders one per row; this
    // one must not, whatever it is styled from.
    expect(container.querySelector('[role="progressbar"]')).toBeNull();
    expect(container.querySelector(".rounded-full")).toBeNull();
  });

  it("states the split as two counts against the repository total", () => {
    render(
      <InferredTestsView
        data={response()}
        untestedFindings={[]}
        onOpenFile={vi.fn()}
      />,
    );

    // Both sides named, and the denominator is files rather than lines: there
    // is no line-level evidence behind this basis to count.
    expect(screen.getByText("1 of 2 files")).toBeInTheDocument();
    expect(screen.getByText(/Nothing reaches the other 1/)).toBeInTheDocument();
  });

  it("says the ranking was trimmed rather than letting it read as the repo", () => {
    const data = response({ files_total: 900 });
    render(
      <InferredTestsView data={data} untestedFindings={[]} onOpenFile={vi.fn()} />,
    );

    expect(screen.getByText(/of 900 files, capped/)).toBeInTheDocument();
  });

  it("frames a report as added resolution, not as a prerequisite", () => {
    render(
      <InferredTestsView
        data={response()}
        untestedFindings={[]}
        onOpenFile={vi.fn()}
      />,
    );

    expect(screen.getByText(/Sharpen this with a coverage report/)).toBeInTheDocument();
    expect(screen.getByText(/needs no setup/)).toBeInTheDocument();
    // The old dead-end line, which stopped being true once the graph answered.
    expect(screen.queryByText(/Nothing is inferred/)).not.toBeInTheDocument();
  });
});

describe("TestsReachingList", () => {
  const answer = (partial: Partial<TestsReachingFile> = {}): TestsReachingFile => ({
    file_path: "src/a.py",
    basis: "inferred",
    reached: true,
    tests: ["tests/test_a.py", "tests/test_b.py"],
    via: "call-graph",
    ...partial,
  });

  it("states the relationship and names the tests", async () => {
    render(
      <TestsReachingList
        filePath="src/a.py"
        cacheKey="k1"
        fetcher={async () => answer()}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText("2 test files")).toBeInTheDocument(),
    );
    expect(screen.getByText("tests/test_a.py")).toBeInTheDocument();
    expect(screen.getByText(/run into this file/)).toBeInTheDocument();
  });

  it("says an import-only answer is the weaker claim", async () => {
    render(
      <TestsReachingList
        filePath="src/a.py"
        cacheKey="k2"
        fetcher={async () => answer({ via: "import-graph" })}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText(/import this file/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/without necessarily running any of it/)).toBeInTheDocument();
  });

  it("names the static limits when nothing reaches the file", async () => {
    render(
      <TestsReachingList
        filePath="src/lonely.py"
        cacheKey="k3"
        fetcher={async () => answer({ reached: false, tests: [], via: null })}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText(/No test in the repository calls into this file/)).toBeInTheDocument(),
    );
    // The caveat is the point: a framework hook is invisible to a static walk,
    // and claiming "untested" without saying so would overstate the evidence.
    expect(screen.getByText(/framework hook/)).toBeInTheDocument();
  });

  it("names the true total when the list was cut, not the length shown", async () => {
    // The cap is an alphabetical slice, so printing tests.length would state
    // the cap as the answer and hide which evidence was dropped.
    render(
      <TestsReachingList
        filePath="src/a.py"
        cacheKey="k5"
        fetcher={async () => answer({ total: 124, truncated: true })}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText("124 test files")).toBeInTheDocument(),
    );
    expect(screen.getByText(/listing 2 of 124, cut alphabetically/)).toBeInTheDocument();
  });

  it("renders nothing when the host has not wired the endpoint", () => {
    const { container } = render(
      <TestsReachingList filePath="src/a.py" cacheKey="k4" />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
