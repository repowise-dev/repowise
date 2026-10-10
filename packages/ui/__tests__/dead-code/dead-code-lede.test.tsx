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
  analyzed_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
};

describe("DeadCodeLede", () => {
  it("leads with the reclaimable line count", () => {
    render(<DeadCodeLede summary={SUMMARY} />);
    expect(screen.getByText("4,321")).toBeInTheDocument();
    expect(screen.getByText("lines")).toBeInTheDocument();
  });

  it("says the posture in one sentence, with when it was measured", () => {
    render(<DeadCodeLede summary={SUMMARY} />);
    const prose = screen.getByText(/have no reachable caller/);
    expect(prose.textContent).toBe(
      "142 findings across 91,234 lines have no reachable caller. Analysed 2d ago.",
    );
  });

  it("carries no stat ribbon or confidence bar repeating the sentence", () => {
    render(<DeadCodeLede summary={SUMMARY} />);
    expect(screen.queryByText("High confidence")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("89 high confidence")).not.toBeInTheDocument();
    // The methodology sits behind the label's info tip instead of in prose.
    expect(screen.getByRole("button", { name: "What Reclaimable means" })).toBeInTheDocument();
  });

  it("drops the time rather than inventing one when the run is unknown", () => {
    render(<DeadCodeLede summary={{ ...SUMMARY, analyzed_at: null }} />);
    expect(screen.getByText(/have no reachable caller/).textContent).not.toMatch(/Analysed/);
  });

  it("agrees in number for a single finding", () => {
    render(
      <DeadCodeLede
        summary={{ ...SUMMARY, total_findings: 1, total_lines: 12, deletable_lines: 1, analyzed_at: null }}
      />,
    );
    expect(screen.getByText(/no reachable caller/).textContent).toBe(
      "1 finding across 12 lines has no reachable caller.",
    );
  });

  it("shows flagged lines, not a zero, when nothing is deletion-ready", () => {
    render(<DeadCodeLede summary={{ ...SUMMARY, total_findings: 47, total_lines: 1669, deletable_lines: 0 }} />);
    expect(screen.getByText("1,669")).toBeInTheDocument();
    expect(screen.getByText(/have no reachable caller/).textContent).toBe(
      "47 findings have no reachable caller. None is deletion-ready yet, so review them before deleting. Analysed 2d ago.",
    );
    expect(screen.getByRole("button", { name: "What Flagged means" })).toBeInTheDocument();
  });
});
