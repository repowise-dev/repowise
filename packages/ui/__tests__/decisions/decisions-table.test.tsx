import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DecisionsTable } from "../../src/decisions/decisions-table.js";
import type { DecisionRecord } from "@repowise/types/decisions";

function makeDecision(overrides: Partial<DecisionRecord> = {}): DecisionRecord {
  return {
    id: "d1",
    repository_id: "r1",
    title: "Pick Postgres",
    status: "active",
    context: "",
    decision: "",
    rationale: "",
    alternatives: [],
    consequences: [],
    affected_files: [],
    affected_modules: [],
    tags: ["infra"],
    source: "git_archaeology",
    evidence_commits: [],
    evidence_file: null,
    evidence_line: null,
    confidence: 0.9,
    staleness_score: 0,
    superseded_by: null,
    last_code_change: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("DecisionsTable", () => {
  const baseProps = {
    repoId: "r1",
    filters: { status: "all" as const, source: "all" as const },
    onFiltersChange: vi.fn(),
  };

  it("renders one row per decision", () => {
    render(
      <DecisionsTable
        {...baseProps}
        decisions={[
          makeDecision({ id: "1", title: "Pick Postgres" }),
          makeDecision({ id: "2", title: "Adopt SWR" }),
        ]}
      />,
    );
    expect(screen.getByText("Pick Postgres")).toBeInTheDocument();
    expect(screen.getByText("Adopt SWR")).toBeInTheDocument();
  });

  it("invokes onFiltersChange when the status filter changes", () => {
    const onFiltersChange = vi.fn();
    render(
      <DecisionsTable {...baseProps} onFiltersChange={onFiltersChange} decisions={[]} />,
    );
    fireEvent.change(screen.getByLabelText("Filter by status"), {
      target: { value: "active" },
    });
    expect(onFiltersChange).toHaveBeenCalledWith({
      status: "active",
      source: "all",
    });
  });

  it("renders a retry button when an error is supplied with no decisions", () => {
    const onRetry = vi.fn();
    render(
      <DecisionsTable
        {...baseProps}
        decisions={[]}
        error={new Error("boom")}
        onRetry={onRetry}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders an empty-state message when there are no decisions and no error", () => {
    render(<DecisionsTable {...baseProps} decisions={[]} />);
    expect(screen.getByText("No decisions found")).toBeInTheDocument();
  });
});
