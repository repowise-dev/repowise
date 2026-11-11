import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AnalysedAt, DeadCodeLede } from "../../src/dead-code/dead-code-lede.js";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";

const SUMMARY: DeadCodeSummary = {
  total_findings: 142,
  confidence_summary: { high: 89, medium: 41, low: 12 },
  deletable_lines: 4321,
  safe_to_delete_count: 12,
  total_lines: 91234,
  by_kind: { unreachable_file: 12, unused_export: 88, zombie_package: 42 },
  analyzed_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
};

const prose = () => screen.getByText(/deletion-ready/, { selector: "span" }).closest("p")!;

describe("DeadCodeLede", () => {
  it("leads with the deletion-ready line count, not the flagged total", () => {
    render(<DeadCodeLede summary={SUMMARY} />);
    expect(screen.getByText("4,321")).toBeInTheDocument();
    expect(screen.queryByText("91,234")).not.toBeInTheDocument();
    expect(screen.getByText("lines")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "What Deletion-ready means" })).toBeInTheDocument();
  });

  it("names the deletion-ready findings, then the flagged rest as a quieter fact", () => {
    render(<DeadCodeLede summary={SUMMARY} />);
    expect(prose().textContent).toBe(
      "12 findings are deletion-ready. 130 more are flagged for review. Analysed 2d ago.",
    );
    expect(screen.getByText("130 more are flagged for review.").className).toMatch(/text-tertiary/);
  });

  it("drops the flagged sentence when every finding is deletion-ready", () => {
    render(
      <DeadCodeLede
        summary={{ ...SUMMARY, total_findings: 1, safe_to_delete_count: 1, analyzed_at: null }}
      />,
    );
    expect(prose().textContent).toBe("1 finding is deletion-ready.");
  });

  it("says plainly when nothing is deletion-ready, with the flagged total", () => {
    render(
      <DeadCodeLede
        summary={{ ...SUMMARY, total_findings: 17, total_lines: 2029, deletable_lines: 0, safe_to_delete_count: 0 }}
      />,
    );
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(prose().textContent).toBe(
      "Nothing is deletion-ready right now. 17 findings across 2,029 lines are flagged for review. Analysed 2d ago.",
    );
  });

  it("counts findings when the deletion-ready ones have no known size", () => {
    render(<DeadCodeLede summary={{ ...SUMMARY, deletable_lines: 0, safe_to_delete_count: 2 }} />);
    expect(prose().textContent).toMatch(/^2 findings are deletion-ready\. 140 more/);
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("findings")).toBeInTheDocument();
    expect(screen.queryByText("lines")).not.toBeInTheDocument();
  });

  it("joins the sentences with the host's separator", () => {
    render(<DeadCodeLede summary={{ ...SUMMARY, analyzed_at: null }} labels={{ separator: "" }} />);
    expect(prose().textContent).toBe("12 findings are deletion-ready.130 more are flagged for review.");
  });

  it("falls back to the lines for a host that sends no count", () => {
    const { safe_to_delete_count: _omit, ...older } = SUMMARY;
    render(<DeadCodeLede summary={{ ...older, analyzed_at: null }} />);
    expect(prose().textContent).toBe(
      "These lines are deletion-ready. 142 findings across 91,234 lines are flagged in all.",
    );
  });

  it("treats a host with no count and no lines as nothing deletion-ready", () => {
    const { safe_to_delete_count: _omit, ...older } = SUMMARY;
    render(<DeadCodeLede summary={{ ...older, deletable_lines: 0, analyzed_at: null }} />);
    expect(prose().textContent).toBe(
      "Nothing is deletion-ready right now. 142 findings across 91,234 lines are flagged for review.",
    );
  });

  it("takes translated copy from the host", () => {
    render(
      <DeadCodeLede
        summary={{ ...SUMMARY, analyzed_at: null }}
        labels={{ label: "可删除", ready: (n) => `${n} 项发现可删除。`, moreFlagged: (n) => `另有 ${n} 项已标记，待审查。` }}
      />,
    );
    expect(screen.getByText("12 项发现可删除。")).toBeInTheDocument();
    expect(screen.getByText("另有 130 项已标记，待审查。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "What 可删除 means" })).toBeInTheDocument();
  });

  it("carries no stat ribbon or confidence bar repeating the sentence", () => {
    render(<DeadCodeLede summary={SUMMARY} />);
    expect(screen.queryByText("High confidence")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("89 high confidence")).not.toBeInTheDocument();
  });

  it("drops the time rather than inventing one when the run is unknown", () => {
    render(<DeadCodeLede summary={{ ...SUMMARY, analyzed_at: null }} />);
    expect(prose().textContent).not.toMatch(/Analysed/);
  });

  it("renders no time for a host that does not report one", () => {
    const { container } = render(<AnalysedAt at={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
