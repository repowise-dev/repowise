/**
 * The flow panel's job is to stop a trace from reading as an ending.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GraphFlowPanel } from "../../src/graph/graph-flow-panel";
import type { ExecutionFlowEntry, ExecutionFlows } from "@repowise-dev/types/graph";

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

function flows(...entries: ExecutionFlowEntry[]): ExecutionFlows {
  return { total_entry_points: entries.length, flows: entries };
}

const noop = () => {};

describe("GraphFlowPanel", () => {
  it("says why the selected trace stopped", () => {
    render(
      <GraphFlowPanel
        flows={flows(flow({ termination: "depth_limit" }))}
        activeFlowIdx={0}
        onSelect={noop}
        onClose={noop}
        missingCount={0}
      />,
    );
    expect(screen.getByText(/stopped at its depth limit/i)).toBeInTheDocument();
  });

  it("stays silent about the stop until a flow is selected", () => {
    // Ten sentences stacked in a 16rem panel is not a reading surface.
    render(
      <GraphFlowPanel
        flows={flows(flow({ termination: "depth_limit" }))}
        activeFlowIdx={null}
        onSelect={noop}
        onClose={noop}
        missingCount={0}
      />,
    );
    expect(screen.queryByText(/depth limit/i)).not.toBeInTheDocument();
  });

  it("counts the hops that rest on a name alone", () => {
    render(
      <GraphFlowPanel
        flows={flows(flow({ trace_via: ["same_file", "global_unique"] }))}
        activeFlowIdx={null}
        onSelect={noop}
        onClose={noop}
        missingCount={0}
      />,
    );
    expect(screen.getByText("1 by name")).toBeInTheDocument();
  });

  it("marks nothing when every hop carries real evidence", () => {
    render(
      <GraphFlowPanel
        flows={flows(flow({ trace_via: ["same_file", "import_scoped"] }))}
        activeFlowIdx={null}
        onSelect={noop}
        onClose={noop}
        missingCount={0}
      />,
    );
    expect(screen.queryByText(/by name/)).not.toBeInTheDocument();
  });

  it("marks nothing on an index that records no origins", () => {
    render(
      <GraphFlowPanel
        flows={flows(flow())}
        activeFlowIdx={null}
        onSelect={noop}
        onClose={noop}
        missingCount={0}
      />,
    );
    expect(screen.queryByText(/by name/)).not.toBeInTheDocument();
    expect(screen.getByText("2 calls")).toBeInTheDocument();
  });

  it("reports the hop count once, not as two figures", () => {
    // The row used to print "depth 2" beside "3 nodes" — one number said twice.
    render(
      <GraphFlowPanel
        flows={flows(flow())}
        activeFlowIdx={null}
        onSelect={noop}
        onClose={noop}
        missingCount={0}
      />,
    );
    expect(screen.queryByText(/nodes/)).not.toBeInTheDocument();
  });

  it("passes the clicked row's index up", () => {
    const onSelect = vi.fn();
    render(
      <GraphFlowPanel
        flows={flows(
          flow(),
          flow({ entry_point: "src/app.py::other", entry_point_name: "other" }),
        )}
        activeFlowIdx={null}
        onSelect={onSelect}
        onClose={noop}
        missingCount={0}
      />,
    );
    fireEvent.click(screen.getByText("other"));
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  it("still warns when the canvas does not hold the whole trace", () => {
    render(
      <GraphFlowPanel
        flows={flows(flow())}
        activeFlowIdx={0}
        onSelect={noop}
        onClose={noop}
        missingCount={2}
      />,
    );
    expect(screen.getByText(/includes 2 nodes not in the loaded view/i)).toBeInTheDocument();
  });

  it("prefers the missing-node warning over the stop, and shows one line", () => {
    // Two stacked paragraphs grow a panel that floats over the canvas, and
    // naming where a trace stopped is misleading while its nodes are off-view.
    render(
      <GraphFlowPanel
        flows={flows(flow({ termination: "no_callees" }))}
        activeFlowIdx={0}
        onSelect={noop}
        onClose={noop}
        missingCount={1}
      />,
    );
    expect(screen.getByText(/includes 1 node not in the loaded view/i)).toBeInTheDocument();
    expect(screen.queryByText(/No outgoing calls were recorded/i)).not.toBeInTheDocument();
  });
});
