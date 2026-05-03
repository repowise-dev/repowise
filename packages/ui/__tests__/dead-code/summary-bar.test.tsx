import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SummaryBar } from "../../src/dead-code/summary-bar.js";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";

const SUMMARY: DeadCodeSummary = {
  total_findings: 142,
  confidence_summary: { high: 89, medium: 41, low: 12 },
  deletable_lines: 4321,
  total_lines: 91234,
  by_kind: { unreachable_file: 12, unused_export: 88, zombie_package: 42 },
};

describe("SummaryBar", () => {
  it("renders the headline counts", () => {
    render(<SummaryBar summary={SUMMARY} />);
    expect(screen.getByText("142")).toBeInTheDocument();
    expect(screen.getByText("4,321")).toBeInTheDocument();
  });

  it("breaks out by_kind and confidence_summary entries", () => {
    render(<SummaryBar summary={SUMMARY} />);
    expect(screen.getByText("unreachable file")).toBeInTheDocument();
    expect(screen.getByText("unused export")).toBeInTheDocument();
    expect(screen.getByText("zombie package")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("89")).toBeInTheDocument();
  });
});
