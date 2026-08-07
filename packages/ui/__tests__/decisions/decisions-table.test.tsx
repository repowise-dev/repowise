import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DecisionsTable } from "../../src/decisions/decisions-table.js";
import type { DecisionRecord } from "@repowise-dev/types/decisions";

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

  it("draws neither a scope nor a confidence column", () => {
    // Both were the same axis as a column already on the row. Scope is
    // `cross-module` on three quarters of a live index, and confidence is
    // source rank times verification, so every record from one source carried
    // one number. Asserted on the headers rather than the cells, because a
    // cell value can coincide with a title.
    render(
      <DecisionsTable
        {...baseProps}
        decisions={[
          makeDecision({ id: "1", scope: "file", confidence: 0.84 }),
          makeDecision({ id: "2", title: "Adopt SWR", scope: "cross-module" }),
        ]}
      />,
    );
    expect(
      screen.queryByRole("columnheader", { name: "Scope" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Confidence" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("84%")).not.toBeInTheDocument();
  });

  it("never draws a trust column, whatever the page happens to hold", () => {
    // Making the column conditional on the loaded window was the first cut and
    // it changed the table's shape between clicks of Next. Both pages here.
    for (const rows of [
      [makeDecision({ id: "1", verification: "exact" })],
      [makeDecision({ id: "2", verification: "fuzzy" })],
    ]) {
      const { unmount } = render(
        <DecisionsTable {...baseProps} decisions={rows} />,
      );
      expect(
        screen.queryByRole("columnheader", { name: "Trust" }),
      ).not.toBeInTheDocument();
      unmount();
    }
  });

  it("marks only the loosely-matched row, beside its own title", () => {
    render(
      <DecisionsTable
        {...baseProps}
        decisions={[
          makeDecision({ id: "1", title: "Exact one", verification: "exact" }),
          makeDecision({ id: "2", title: "Loose one", verification: "fuzzy" }),
        ]}
      />,
    );
    // `iconOnly` puts the tier in an sr-only label, which is also what a
    // screen reader gets. It must sit with the row it qualifies.
    const mark = screen.getByText("Fuzzy match");
    expect(mark.closest("td")?.textContent).toContain("Loose one");
    expect(mark.closest("td")?.textContent).not.toContain("Exact one");
    expect(screen.queryByText("Verified quote")).not.toBeInTheDocument();
  });

  it("offers every live source in the filter and no retired one", () => {
    // The hardcoded list offered `readme_mining` and `cli` while omitting
    // `pr` and `session`, which are 86% of a live index, so the control could
    // not reach the records worth reaching and could only ever empty the
    // table. Options are not read off the loaded page either: page one of
    // fifty need not carry every source.
    render(
      <DecisionsTable
        {...baseProps}
        decisions={[
          makeDecision({ id: "1", source: "git_archaeology" }),
          // A legacy row predating the purge. Without this the second filter
          // is unreachable and deleting it leaves the test green.
          makeDecision({ id: "2", source: "readme_mining" as never }),
        ]}
      />,
    );
    const select = screen.getByLabelText("Filter by source");
    const values = Array.from(
      select.querySelectorAll("option"),
      (o) => (o as HTMLOptionElement).value,
    );
    expect(values).toContain("pr");
    expect(values).toContain("session");
    expect(values).toContain("adr");
    expect(values).not.toContain("readme_mining");
    expect(values).not.toContain("changelog");
    expect(values).not.toContain("code_comment");
    // `comment` is a live source and `code_comment` is the retired one. They
    // are different values, and dropping both would lose five real records.
    expect(values).toContain("comment");
  });

  it("surfaces an unmapped source the engine has started emitting", () => {
    render(
      <DecisionsTable
        {...baseProps}
        decisions={[makeDecision({ id: "1", source: "issue_tracker" as never })]}
      />,
    );
    const values = Array.from(
      screen.getByLabelText("Filter by source").querySelectorAll("option"),
      (o) => (o as HTMLOptionElement).value,
    );
    expect(values).toContain("issue_tracker");
  });

  it("no longer renders a scope filter control", () => {
    // The prop and the filtering below it are kept for hosts that pass one;
    // the control is gone with the column, because a reader using it would
    // watch rows vanish for a reason nothing on screen explains.
    render(
      <DecisionsTable
        {...baseProps}
        decisions={[makeDecision({ id: "1", scope: "file" })]}
      />,
    );
    expect(screen.queryByLabelText("Filter by scope")).not.toBeInTheDocument();
  });

  it("ignores a scope value when no record carries scope", () => {
    render(
      <DecisionsTable
        {...baseProps}
        filters={{ status: "all", source: "all", scope: "file" }}
        decisions={[
          makeDecision({ id: "1", title: "Pick Postgres" }),
          makeDecision({ id: "2", title: "Adopt SWR" }),
        ]}
      />,
    );
    // Rows are NOT filtered out by the stale scope value.
    expect(screen.getByText("Pick Postgres")).toBeInTheDocument();
    expect(screen.getByText("Adopt SWR")).toBeInTheDocument();
  });

  it("tells apart a record with no files from one whose files have not moved", () => {
    // The defect this replaces: both scored 0.0 and both rendered as the same
    // em dash, so a reader who correctly read one had been taught a rule that
    // made them wrong about the other.
    render(
      <DecisionsTable
        {...baseProps}
        decisions={[
          makeDecision({ id: "1", title: "Unscoped", affected_files: [] }),
          makeDecision({
            id: "2",
            title: "Steady",
            affected_files: ["a.ts", "b.ts"],
            staleness_score: 0,
          }),
          makeDecision({
            id: "3",
            title: "Moved",
            affected_files: ["a.ts", "b.ts", "c.ts", "d.ts"],
            staleness_score: 0.5,
          }),
        ]}
      />,
    );
    expect(screen.getByText("no files")).toBeInTheDocument();
    expect(screen.getByText("0 of 2")).toBeInTheDocument();
    expect(screen.getByText("2 of 4")).toBeInTheDocument();
  });

  it("does not paint a moved scope as an error", () => {
    // Red is reserved for health bands. Three confirmed, still-true working
    // rules sat at a red 100% on a live index because the files they cite
    // happened to change.
    render(
      <DecisionsTable
        {...baseProps}
        decisions={[
          makeDecision({
            id: "1",
            affected_files: ["a.ts"],
            staleness_score: 1,
          }),
        ]}
      />,
    );
    const cell = screen.getByText("1 of 1");
    expect(cell.className).not.toContain("--color-error");
  });

  it("filters rows client-side by scope", () => {
    render(
      <DecisionsTable
        {...baseProps}
        filters={{ status: "all", source: "all", scope: "file" }}
        decisions={[
          makeDecision({ id: "1", title: "Pick Postgres", scope: "file" }),
          makeDecision({ id: "2", title: "Adopt SWR", scope: "cross-module" }),
        ]}
      />,
    );
    expect(screen.getByText("Pick Postgres")).toBeInTheDocument();
    expect(screen.queryByText("Adopt SWR")).not.toBeInTheDocument();
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
