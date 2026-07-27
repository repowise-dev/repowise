import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DeadCodeLede } from "../../src/dead-code/dead-code-lede.js";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";

const SUMMARY: DeadCodeSummary = {
  total_findings: 142,
  confidence_summary: { high: 89, medium: 41, low: 12 },
  deletable_lines: 4321,
  total_lines: 91234,
  by_kind: { unreachable_file: 12, unused_export: 88, zombie_package: 42 },
};

describe("DeadCodeLede", () => {
  it("leads with the reclaimable line count", () => {
    render(<DeadCodeLede summary={SUMMARY} />);
    expect(screen.getByText("4,321")).toBeInTheDocument();
    expect(screen.getByText("lines")).toBeInTheDocument();
  });

  it("names every kind in prose rather than as separate tiles", () => {
    render(<DeadCodeLede summary={SUMMARY} />);
    const prose = screen.getByText(/have no reachable caller/);
    expect(prose.textContent).toContain("88 unused exports");
    expect(prose.textContent).toContain("42 zombie packages");
    expect(prose.textContent).toContain("12 unreachable files");
  });

  it("splits confidence across the ribbon and the bar", () => {
    render(<DeadCodeLede summary={SUMMARY} />);
    // High stands alone; medium and low are the one "verify this" bucket.
    expect(screen.getByText("89")).toBeInTheDocument();
    expect(screen.getByText("53")).toBeInTheDocument();
    expect(screen.getByLabelText("89 high confidence")).toBeInTheDocument();
  });

  it("reports the deletable share of the flagged lines, not of the repo", () => {
    render(<DeadCodeLede summary={SUMMARY} />);
    // 4,321 of the 91,234 lines sitting inside a finding.
    expect(screen.getByText("5%")).toBeInTheDocument();
    expect(screen.getByText("91,234")).toBeInTheDocument();
  });

  it("says a clean repository is a result, not an absence", () => {
    render(
      <DeadCodeLede
        summary={{
          total_findings: 0,
          confidence_summary: { high: 0, medium: 0, low: 0 },
          deletable_lines: 0,
          total_lines: 0,
          by_kind: {},
        }}
      />,
    );
    expect(
      screen.getByText(/Nothing in this repository is currently flagged/),
    ).toBeInTheDocument();
  });
});
