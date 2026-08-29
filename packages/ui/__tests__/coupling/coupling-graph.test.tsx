import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import type { CouplingEdge, CouplingNode } from "@repowise-dev/types/coupling";
import { CouplingGraph } from "../../src/coupling/coupling-graph.js";
import { CouplingTable } from "../../src/coupling/coupling-table.js";

function node(path: string, score: number | null = 7, nloc = 100): CouplingNode {
  return { file_path: path, module: path.split("/")[0] ?? null, score, nloc };
}
function edge(s: string, t: string, strength = 3, last: string | null = "2026-06-01"): CouplingEdge {
  return {
    source: s,
    target: t,
    strength,
    last_co_change: last,
    support: 0,
    confidence_ab: null,
    confidence_ba: null,
    structural: null,
    dependency_kind: null,
  };
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

  it("peeks a file's couplings on hover (onHover fires)", () => {
    const nodes = [node("api/a.py"), node("core/b.py")];
    const edges = [edge("api/a.py", "core/b.py")];
    const onHover = vi.fn();
    const { container } = render(
      <CouplingGraph nodes={nodes} edges={edges} focusedPath={null} onHover={onHover} />,
    );
    const circle = container.querySelector("circle");
    expect(circle).not.toBeNull();
    fireEvent.mouseEnter(circle!.parentElement!);
    expect(onHover).toHaveBeenCalledWith(expect.any(String));
  });

  it("is decorative and names the table as its accessible equivalent", () => {
    const nodes = [node("api/a.py"), node("core/b.py")];
    const edges = [edge("api/a.py", "core/b.py")];
    const { container } = render(
      <CouplingGraph nodes={nodes} edges={edges} tableId="pairs" />,
    );
    // No tab stop and no operable role, so it must not announce itself as one.
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg.getAttribute("role")).toBeNull();
    expect(screen.getByRole("link", { name: /the table below/i })).toHaveAttribute(
      "href",
      "#pairs",
    );
  });

  it("lights only the focused pair's arc, and rings both of its ends", () => {
    const nodes = [node("api/a.py"), node("core/b.py"), node("ui/c.py")];
    const edges = [edge("api/a.py", "core/b.py"), edge("api/a.py", "ui/c.py")];
    const { container } = render(
      <CouplingGraph
        nodes={nodes}
        edges={edges}
        focusedPair={{ source: "api/a.py", target: "ui/c.py" }}
        pinnedPair={{ source: "api/a.py", target: "ui/c.py" }}
      />,
    );
    const arcs = [...container.querySelectorAll('g[fill="none"] > path')];
    const lit = arcs.filter((p) => Number(p.getAttribute("stroke-opacity")) > 0.5);
    // One arc: focusing a.py alone would light both of its couplings.
    expect(lit).toHaveLength(1);
    // A persistent ring on each end.
    expect(container.querySelectorAll('circle[stroke="var(--color-accent-primary)"]')).toHaveLength(
      2,
    );
  });

  it("keeps hub labels when a file is focused instead of swapping them out", () => {
    const nodes = [node("api/a.py"), node("core/b.py"), node("ui/c.py")];
    const edges = [edge("api/a.py", "core/b.py"), edge("core/b.py", "ui/c.py")];
    const { container, rerender } = render(
      <CouplingGraph nodes={nodes} edges={edges} focusedPath={null} />,
    );
    const unfocused = container.querySelectorAll("svg text").length;
    rerender(<CouplingGraph nodes={nodes} edges={edges} focusedPath="api/a.py" />);
    expect(container.querySelectorAll("svg text").length).toBeGreaterThanOrEqual(unfocused);
  });

  it("pins a file on click (onPinToggle fires with the path)", () => {
    const nodes = [node("api/a.py"), node("core/b.py")];
    const edges = [edge("api/a.py", "core/b.py")];
    const onPinToggle = vi.fn();
    const { container } = render(
      <CouplingGraph
        nodes={nodes}
        edges={edges}
        focusedPath={null}
        pinnedPath={null}
        onPinToggle={onPinToggle}
      />,
    );
    const circle = container.querySelector("circle");
    fireEvent.click(circle!.parentElement!);
    expect(onPinToggle).toHaveBeenCalledWith(expect.any(String));
  });
});

describe("CouplingTable", () => {
  // Stacked-card mode keeps a second copy of every row in the DOM; scope to the
  // table so a query does not match both trees.
  const inTable = () => within(screen.getByRole("table"));

  it("renders a row per coupling, strongest first", () => {
    const edges = [edge("a.py", "b.py", 4), edge("c.py", "d.py", 1)];
    render(<CouplingTable edges={edges} />);
    expect(inTable().getByText("a.py")).toBeInTheDocument();
    expect(inTable().getByText("↔ b.py")).toBeInTheDocument();
  });

  it("toggles the pin on row click", () => {
    const onPinToggle = vi.fn();
    render(<CouplingTable edges={[edge("a.py", "b.py", 4)]} pinnedPath={null} onPinToggle={onPinToggle} />);
    fireEvent.click(inTable().getByText("a.py"));
    expect(onPinToggle).toHaveBeenCalledWith("a.py");
  });

  it("shows the empty state with no couplings", () => {
    render(<CouplingTable edges={[]} />);
    expect(screen.getByText(/no couplings detected/i)).toBeInTheDocument();
  });
});
