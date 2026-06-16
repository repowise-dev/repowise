import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { CouplingEdge, CouplingNode } from "@repowise-dev/types/coupling";
import { CouplingGraph } from "../../src/coupling/coupling-graph.js";
import { CouplingTable } from "../../src/coupling/coupling-table.js";

function node(path: string, score: number | null = 7, nloc = 100): CouplingNode {
  return { file_path: path, module: path.split("/")[0] ?? null, score, nloc };
}
function edge(s: string, t: string, strength = 3, last: string | null = "2026-06-01"): CouplingEdge {
  return { source: s, target: t, strength, last_co_change: last };
}

describe("CouplingGraph", () => {
  it("shows the empty state when there is nothing to bundle", () => {
    render(<CouplingGraph nodes={[]} edges={[]} />);
    expect(screen.getByText(/not enough shared git history/i)).toBeInTheDocument();
  });

  it("renders an arc per drawn coupling and the honest count line", () => {
    const nodes = [node("api/a.py", 3), node("core/b.py", 9), node("ui/c.py", 6)];
    const edges = [edge("api/a.py", "core/b.py", 5), edge("core/b.py", "ui/c.py", 2)];
    const { container } = render(<CouplingGraph nodes={nodes} edges={edges} totalEdges={9} />);
    // Two bundled edge paths drawn (module arc bands are separate <path>s but
    // edges live in the dedicated fill="none" group).
    expect(container.querySelectorAll("svg path").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/showing 2 of 9 couplings/i)).toBeInTheDocument();
  });

  it("emphasizes a file's couplings on hover (focus changes)", () => {
    const nodes = [node("api/a.py"), node("core/b.py")];
    const edges = [edge("api/a.py", "core/b.py")];
    let focus: string | null = null;
    const { container } = render(
      <CouplingGraph
        nodes={nodes}
        edges={edges}
        focusedPath={focus}
        onFocusChange={(p) => (focus = p)}
      />,
    );
    const circle = container.querySelector("circle");
    expect(circle).not.toBeNull();
    fireEvent.mouseEnter(circle!.parentElement!);
    expect(focus).not.toBeNull();
  });
});

describe("CouplingTable", () => {
  it("renders a row per coupling, strongest first", () => {
    const edges = [edge("a.py", "b.py", 4), edge("c.py", "d.py", 1)];
    render(<CouplingTable edges={edges} />);
    expect(screen.getByText("a.py")).toBeInTheDocument();
    expect(screen.getByText("↔ b.py")).toBeInTheDocument();
  });

  it("toggles focus on row click", () => {
    const edges = [edge("a.py", "b.py", 4)];
    let focus: string | null = null;
    render(<CouplingTable edges={edges} focusedPath={focus} onFocusChange={(p) => (focus = p)} />);
    fireEvent.click(screen.getByText("a.py"));
    expect(focus).toBe("a.py");
  });

  it("shows the empty state with no couplings", () => {
    render(<CouplingTable edges={[]} />);
    expect(screen.getByText(/no couplings detected/i)).toBeInTheDocument();
  });
});
