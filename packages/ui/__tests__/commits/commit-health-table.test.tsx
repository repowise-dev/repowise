import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CommitHealthFinding } from "@repowise-dev/types/git";
import { CommitHealthTable } from "../../src/commits/commit-health-table";

function finding(partial: Partial<CommitHealthFinding>): CommitHealthFinding {
  return {
    change_kind: "worsened",
    dimension: "defect",
    biomarker_type: "complex_method",
    severity: "high",
    path: "core/health/engine.py",
    symbol: "evaluate",
    line_start: 40,
    attribution_basis: "added_lines",
    reason: "evaluate has cyclomatic complexity 22",
    ...partial,
  };
}

describe("CommitHealthTable attribution", () => {
  it("says where a moved finding came from instead of calling it incidental", () => {
    render(
      <CommitHealthTable
        findings={[
          finding({
            attribution_basis: "moved",
            moved_from: { path: "core/health/old_engine.py", symbol: "evaluate" },
          }),
        ]}
      />,
    );
    expect(screen.getAllByText(/moved here from evaluate in core\/health\/old_engine\.py/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/not on a line it wrote/)).toBeNull();
  });

  it("names the moved basis even without the source path", () => {
    render(<CommitHealthTable findings={[finding({ attribution_basis: "moved" })]} />);
    expect(screen.getAllByText(/moved here from another file/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/not on a line it wrote/)).toBeNull();
  });

  it("keeps the incidental note for a file the commit only touched", () => {
    render(<CommitHealthTable findings={[finding({ attribution_basis: "file_change" })]} />);
    expect(screen.getAllByText(/not on a line it wrote/).length).toBeGreaterThan(0);
  });

  it("adds no note when the commit wrote the line", () => {
    render(<CommitHealthTable findings={[finding({ attribution_basis: "added_lines" })]} />);
    expect(screen.queryByText(/\(.*\)/)).toBeNull();
  });

  it("says a call-path finding was reached through a call the commit edited", () => {
    render(<CommitHealthTable findings={[finding({ attribution_basis: "changed_call_edge" })]} />);
    expect(screen.getAllByText(/reached through a call this commit edited/).length).toBeGreaterThan(0);
  });

  it("says an unexplained finding is not explained by the diff", () => {
    render(<CommitHealthTable findings={[finding({ attribution_basis: "unknown" })]} />);
    expect(screen.getAllByText(/not explained by this commit's diff/).length).toBeGreaterThan(0);
  });
});
