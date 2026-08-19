import { describe, it, expect, vi } from "vitest";
import { render, waitFor, screen } from "@testing-library/react";
import type {
  CouplingEdge,
  CouplingNode,
  CouplingGraphResponse,
} from "@repowise-dev/types/coupling";
import { CouplingExplorer } from "../../src/coupling/coupling-explorer.js";

function node(path: string): CouplingNode {
  return {
    file_path: path,
    module: path.split("/")[0] ?? null,
    score: 5,
    nloc: 100,
  };
}

function edge(s: string, t: string, strength = 3): CouplingEdge {
  return {
    source: s,
    target: t,
    strength,
    last_co_change: "2026-06-01",
  };
}

function graph(
  nodes: CouplingNode[],
  edges: CouplingEdge[],
): CouplingGraphResponse {
  return { nodes, edges, total_edges: edges.length };
}

describe("CouplingExplorer", () => {
  it("reports pin invalidation through onFocusChange when the file leaves the payload", async () => {
    const onFocusChange = vi.fn();
    const initial = graph(
      [node("a.py"), node("b.py"), node("gone.py")],
      [edge("a.py", "b.py", 5), edge("a.py", "gone.py", 2)],
    );
    const { rerender } = render(
      <CouplingExplorer
        data={initial}
        initialFocus="gone.py"
        onFocusChange={onFocusChange}
      />,
    );

    const next = graph(
      [node("a.py"), node("b.py")],
      [edge("a.py", "b.py", 5)],
    );
    rerender(
      <CouplingExplorer
        data={next}
        initialFocus="gone.py"
        onFocusChange={onFocusChange}
      />,
    );

    await waitFor(() => {
      expect(onFocusChange).toHaveBeenCalledWith(null);
    });
  });

  it("treats an empty initialFocus as absent and seeds the strict top hub", () => {
    // hub.py degree 2; a.py / b.py degree 1 — unique maximum
    const data = graph(
      [node("hub.py"), node("a.py"), node("b.py")],
      [edge("a.py", "hub.py", 3), edge("b.py", "hub.py", 2)],
    );
    render(<CouplingExplorer data={data} initialFocus="" />);
    const guidance = screen.getByText(/Tracing/i);
    expect(guidance).toHaveTextContent("Tracing hub.py");
  });
});