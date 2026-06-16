import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { FileSignals } from "@repowise-dev/types/health";
import { FileSignalsPanel } from "../../src/health/file-signals-panel.js";

function sig(partial: Partial<FileSignals>): FileSignals {
  return {
    prior_defect_count: null,
    change_entropy_pct: null,
    lines_added_90d: null,
    lines_deleted_90d: null,
    commit_count_90d: null,
    age_days: null,
    primary_owner_name: null,
    primary_owner_commit_pct: null,
    recent_owner_name: null,
    recent_owner_commit_pct: null,
    in_degree: null,
    out_degree: null,
    ...partial,
  };
}

describe("FileSignalsPanel", () => {
  it("renders populated process / people / topology signals", () => {
    render(
      <FileSignalsPanel
        signals={sig({
          prior_defect_count: 3,
          commit_count_90d: 7,
          lines_added_90d: 120,
          lines_deleted_90d: 40,
          primary_owner_name: "Ada",
          primary_owner_commit_pct: 0.62,
          in_degree: 17,
          out_degree: 4,
        })}
      />,
    );
    expect(screen.getByText("Process")).toBeInTheDocument();
    expect(screen.getByText("People")).toBeInTheDocument();
    expect(screen.getByText("Topology")).toBeInTheDocument();
    expect(screen.getByText(/3 bug-fixes/)).toBeInTheDocument();
    expect(screen.getByText(/7 commits · \+120 \/ −40/)).toBeInTheDocument();
    expect(screen.getByText(/Ada \(62%\)/)).toBeInTheDocument();
    expect(screen.getByText(/17 files depend on this/)).toBeInTheDocument();
    expect(screen.getByText(/depends on 4 files/)).toBeInTheDocument();
  });

  it("reports zero bug-fixes as a real signal, not 'No signal'", () => {
    render(<FileSignalsPanel signals={sig({ prior_defect_count: 0, commit_count_90d: 2 })} />);
    expect(screen.getByText("No bug-fixes")).toBeInTheDocument();
  });

  it("shows 'No signal' for absent fields when some are present", () => {
    render(<FileSignalsPanel signals={sig({ in_degree: 5, out_degree: 0 })} />);
    // Topology present, process/people absent.
    expect(screen.getByText(/5 files depend on this/)).toBeInTheDocument();
    expect(screen.getAllByText("No signal").length).toBeGreaterThan(0);
  });

  it("flags an owner handoff when recent owner differs from primary", () => {
    render(
      <FileSignalsPanel
        signals={sig({
          primary_owner_name: "Ada",
          primary_owner_commit_pct: 0.7,
          recent_owner_name: "Grace",
          recent_owner_commit_pct: 0.5,
        })}
      />,
    );
    expect(screen.getByText(/differs from primary/)).toBeInTheDocument();
    expect(screen.getByText(/Grace \(50%\)/)).toBeInTheDocument();
  });

  it("renders nothing when signals is null", () => {
    const { container } = render(<FileSignalsPanel signals={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when every signal is absent", () => {
    const { container } = render(<FileSignalsPanel signals={sig({})} />);
    expect(container).toBeEmptyDOMElement();
  });
});
