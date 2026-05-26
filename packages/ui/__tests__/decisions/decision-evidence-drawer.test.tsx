import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DecisionEvidenceDrawer } from "../../src/decisions/decision-evidence-drawer.js";
import type { DecisionEvidence } from "@repowise-dev/types/decisions";

function makeEvidence(overrides: Partial<DecisionEvidence> = {}): DecisionEvidence {
  return {
    id: "e1",
    source: "git_archaeology",
    source_rank: 5,
    evidence_file: "src/db.py",
    evidence_line: 42,
    evidence_commit: "abcdef1234567890",
    source_quote: "switch to Postgres for JSONB support",
    confidence: 0.9,
    verification: "exact",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("DecisionEvidenceDrawer", () => {
  it("renders nothing visible when closed", () => {
    render(
      <DecisionEvidenceDrawer open={false} onClose={() => {}} evidence={[makeEvidence()]} />,
    );
    expect(screen.queryByText("switch to Postgres for JSONB support")).not.toBeInTheDocument();
  });

  it("renders an evidence row with quote, file and verification badge when open", () => {
    render(
      <DecisionEvidenceDrawer
        open
        onClose={() => {}}
        decisionTitle="Pick Postgres"
        evidence={[makeEvidence()]}
      />,
    );
    expect(screen.getByText("switch to Postgres for JSONB support")).toBeInTheDocument();
    expect(screen.getByText("Verified quote")).toBeInTheDocument();
    expect(screen.getByText(/src\/db\.py/)).toBeInTheDocument();
  });

  it("sorts rows by source_rank descending", () => {
    render(
      <DecisionEvidenceDrawer
        open
        onClose={() => {}}
        evidence={[
          makeEvidence({ id: "low", source_rank: 1, source_quote: "low rank quote" }),
          makeEvidence({ id: "high", source_rank: 9, source_quote: "high rank quote" }),
        ]}
      />,
    );
    const quotes = screen.getAllByText(/rank quote/);
    expect(quotes[0]).toHaveTextContent("high rank quote");
  });

  it("shows an empty state when there is no evidence", () => {
    render(<DecisionEvidenceDrawer open onClose={() => {}} evidence={[]} />);
    expect(screen.getByText(/No evidence rows/)).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(<DecisionEvidenceDrawer open onClose={onClose} evidence={[makeEvidence()]} />);
    fireEvent.click(screen.getByLabelText("Close evidence drawer"));
    expect(onClose).toHaveBeenCalled();
  });
});
