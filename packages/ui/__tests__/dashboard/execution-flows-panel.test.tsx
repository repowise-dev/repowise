/**
 * The trace chain marks which link is a guess, so its indexing has to be right:
 * `trace_via` is pairwise, and the hop arriving at pill `i` is `trace_via[i-1]`.
 * An off-by-one here blames the wrong call, which is worse than no mark.
 */

import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ExecutionFlowsPanel } from "../../src/dashboard/execution-flows-panel";
import type { ExecutionFlowEntry } from "@repowise-dev/types/graph";

function flow(over: Partial<ExecutionFlowEntry> = {}): ExecutionFlowEntry {
  return {
    entry_point: "src/app.py::handle",
    entry_point_name: "handle",
    entry_point_score: 0.9,
    trace: ["src/app.py::handle", "src/app.py::validate", "src/app.py::persist"],
    depth: 2,
    crosses_community: false,
    communities_visited: [1],
    ...over,
  };
}

function renderPanel(entry: ExecutionFlowEntry) {
  return render(<ExecutionFlowsPanel flows={[entry]} repoId="r1" />);
}

describe("ExecutionFlowsPanel", () => {
  it("renders an empty state with no flows", () => {
    render(<ExecutionFlowsPanel flows={[]} repoId="r1" />);
    expect(screen.getByText("No execution flows")).toBeInTheDocument();
  });

  it("keys the dashed connector only when a shown hop is a name match", () => {
    const { container } = renderPanel(flow({ trace_via: ["same_file", "global_unique"] }));
    // The chain only renders once expanded.
    fireEvent.click(screen.getByText("handle"));
    expect(container.querySelectorAll(".border-dashed").length).toBeGreaterThan(0);
  });

  it("shows no key when every shown hop carries real evidence", () => {
    const { container } = renderPanel(flow({ trace_via: ["same_file", "import_scoped"] }));
    fireEvent.click(screen.getByText("handle"));
    expect(screen.queryByText("resolved by name match")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".border-dashed").length).toBe(0);
  });

  it("shows no key on an index that records no origins", () => {
    const { container } = renderPanel(flow());
    fireEvent.click(screen.getByText("handle"));
    expect(screen.queryByText("resolved by name match")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".border-dashed").length).toBe(0);
  });

  it("names a name-match hop for a reader who cannot see the dash", () => {
    renderPanel(flow({ trace_via: ["global_unique", "same_file"] }));
    fireEvent.click(screen.getByText("handle"));
    expect(screen.getByText(/resolved by name match:/)).toBeInTheDocument();
  });

  it("collapsed rows say nothing about the stop", () => {
    renderPanel(flow({ termination: "depth_limit" }));
    expect(screen.queryByText(/depth limit/i)).not.toBeInTheDocument();
  });
});
