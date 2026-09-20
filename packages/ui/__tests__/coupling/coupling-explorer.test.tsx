import { describe, it, expect, vi } from "vitest";
import { render, waitFor, screen, fireEvent, within } from "@testing-library/react";
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
    support: 0,
    confidence_ab: null,
    confidence_ba: null,
    structural: null,
    dependency_kind: null,
  };
}

function graph(
  nodes: CouplingNode[],
  edges: CouplingEdge[],
): CouplingGraphResponse {
  return {
    nodes,
    edges,
    total_edges: edges.length,
    coupled_files: nodes.length,
    total_files: nodes.length,
  };
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

// The arcs live in the diagram's dedicated `fill="none"` group; the module
// bands are separate paths outside it.
const arcs = (container: HTMLElement) =>
  container.querySelectorAll('g[fill="none"] > path').length;
const inTable = () => within(screen.getByRole("table"));

function labelled(
  s: string,
  t: string,
  structural: CouplingEdge["structural"],
  strength = 3,
): CouplingEdge {
  return { ...edge(s, t, strength), structural };
}

describe("CouplingExplorer structural segments", () => {
  // A lockfile pair that outranks everything on strength, and the real finding.
  const plumbing = labelled("pyproject.toml", "uv.lock", "not_applicable", 90);
  const explained = labelled("core/a.py", "core/b.py", "corroborated", 50);
  const finding = labelled("core/c.py", "server/d.py", "unexplained", 10);
  const data = graph(
    [
      node("pyproject.toml"),
      node("uv.lock"),
      node("core/a.py"),
      node("core/b.py"),
      node("core/c.py"),
      node("server/d.py"),
    ],
    [plumbing, explained, finding],
  );

  it("opens on the unexplained segment, so release plumbing is not the headline", () => {
    render(<CouplingExplorer data={data} />);
    expect(inTable().getByText("c.py")).toBeInTheDocument();
    expect(inTable().queryByText("↔ uv.lock")).not.toBeInTheDocument();
    expect(inTable().queryByText("↔ b.py")).not.toBeInTheDocument();
  });

  it("drives the diagram with the same filter as the table", () => {
    const { container } = render(<CouplingExplorer data={data} />);
    // One unexplained pair → one arc, not all three.
    expect(arcs(container)).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: /^All/ }));
    expect(arcs(container)).toBe(3);
  });

  it("narrows the diagram when the search box narrows the table", () => {
    const { container } = render(<CouplingExplorer data={data} />);
    fireEvent.click(screen.getByRole("button", { name: /^All/ }));
    expect(arcs(container)).toBe(3);
    fireEvent.change(screen.getByLabelText(/filter the couplings by file path/i), {
      target: { value: "uv.lock" },
    });
    expect(arcs(container)).toBe(1);
  });

  it("opens on All when nothing is unexplained, rather than on an empty list", () => {
    const noFinding = graph(
      [node("pyproject.toml"), node("uv.lock"), node("core/a.py"), node("core/b.py")],
      [plumbing, explained],
    );
    render(<CouplingExplorer data={noFinding} />);
    expect(inTable().getByText("↔ uv.lock")).toBeInTheDocument();
  });

  it("hides the segments on an index written before the structural check", () => {
    const data = graph([node("a.py"), node("b.py")], [edge("a.py", "b.py")]);
    render(<CouplingExplorer data={data} />);
    expect(screen.queryByRole("group", { name: /dependency graph/i })).not.toBeInTheDocument();
  });
});

describe("CouplingExplorer pair selection", () => {
  const data = graph(
    [node("core/a.py"), node("core/b.py"), node("core/c.py")],
    [edge("core/a.py", "core/b.py", 5), edge("core/a.py", "core/c.py", 2)],
  );

  it("pins both ends of the row's pair and serializes them to the focus", () => {
    const onFocusChange = vi.fn();
    render(<CouplingExplorer data={data} onFocusChange={onFocusChange} />);
    fireEvent.click(inTable().getByText("↔ c.py"));
    expect(onFocusChange).toHaveBeenCalledWith("core/a.py|core/c.py");
    expect(screen.getByText(/Tracing/i)).toHaveTextContent("core/a.py ↔ core/c.py");
  });

  it("reopens a serialized pair from the initial focus", () => {
    render(<CouplingExplorer data={data} initialFocus="core/a.py|core/b.py" />);
    expect(screen.getByText(/Tracing/i)).toHaveTextContent("core/a.py ↔ core/b.py");
  });

  it("opens the pair's detail panel on the same click that pins it", () => {
    render(<CouplingExplorer data={data} repoLinkPrefix="/repos/r" />);
    // Not the file name: those are links and stop propagation so they can
    // navigate. The rest of the row opens the panel.
    fireEvent.click(inTable().getByText("↔ c.py").closest("tr")!);
    const panel = screen.getByRole("dialog");
    // The claim, the labelled AI action, and a route onward to each file.
    expect(within(panel).getByRole("button", { name: /ai decouple prompt/i })).toBeInTheDocument();
    const links = within(panel).getAllByRole("link", { name: /open file page/i });
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", "/repos/r/files/core/a.py");
  });

  it("names both directions separately in the panel", () => {
    const asymmetric = graph(
      [node("core/a.py"), node("core/b.py")],
      [
        {
          ...edge("core/a.py", "core/b.py", 5),
          support: 27,
          confidence_ab: 0.47,
          confidence_ba: 1.0,
          structural: "unexplained",
        },
      ],
    );
    render(<CouplingExplorer data={asymmetric} />);
    fireEvent.click(inTable().getAllByRole("row")[1]!);
    const panel = within(screen.getByRole("dialog"));
    // A single "up to 100%" hides which side owns it; both shares are stated.
    expect(panel.getByText("47%")).toBeInTheDocument();
    expect(panel.getByText("100%")).toBeInTheDocument();
    expect(panel.getByText(/47% of its commits also touched b\.py/)).toBeInTheDocument();
    expect(panel.getByText("Unexplained")).toBeInTheDocument();
  });

  it("names the dependency behind an explained pair", () => {
    const explained = graph(
      [node("core/a.py"), node("core/b.py")],
      [
        {
          ...edge("core/a.py", "core/b.py", 5),
          support: 9,
          structural: "corroborated",
          dependency_kind: "type_use",
        },
      ],
    );
    render(<CouplingExplorer data={explained} />);
    fireEvent.click(inTable().getAllByRole("row")[1]!);
    const panel = within(screen.getByRole("dialog"));
    // "Explained" alone does not say how the graph explains it.
    expect(panel.getByText("Explained")).toBeInTheDocument();
    // Both the verdict chip and the claim sentence name it, from one helper.
    expect(panel.getByText("· a type reference")).toBeInTheDocument();
    expect(
      panel.getByText(/A type reference in the graph already connects them/),
    ).toBeInTheDocument();
  });

  it("says nothing about the kind when the index recorded none", () => {
    const explained = graph(
      [node("core/a.py"), node("core/b.py")],
      [{ ...edge("core/a.py", "core/b.py", 5), support: 9, structural: "corroborated" }],
    );
    render(<CouplingExplorer data={explained} />);
    fireEvent.click(inTable().getAllByRole("row")[1]!);
    const panel = within(screen.getByRole("dialog"));
    expect(panel.getByText("Explained")).toBeInTheDocument();
    expect(panel.getByText(/The dependency graph already connects them/i)).toBeInTheDocument();
  });

  it("gives the coupling total a denominator in files", () => {
    const scaled: CouplingGraphResponse = {
      ...graph([node("core/a.py"), node("core/b.py")], [edge("core/a.py", "core/b.py")]),
      total_edges: 14147,
      coupled_files: 2038,
      total_files: 3787,
    };
    render(<CouplingExplorer data={scaled} />);
    expect(screen.getByText(/files with commit history/)).toHaveTextContent(
      "across 2,038 of 3,787 files with commit history",
    );
  });

  it("omits the denominator when the index does not carry one", () => {
    const older: CouplingGraphResponse = {
      ...graph([node("core/a.py"), node("core/b.py")], [edge("core/a.py", "core/b.py")]),
      coupled_files: 0,
      total_files: 0,
    };
    render(<CouplingExplorer data={older} />);
    expect(screen.queryByText(/files with commit history/)).not.toBeInTheDocument();
    expect(screen.getByText(/in this repository/)).toBeInTheDocument();
  });

  it("treats an unrecognized pipe path as one file, not a pair", () => {
    const odd = graph(
      [node("weird|name.py"), node("core/b.py")],
      [edge("core/b.py", "weird|name.py")],
    );
    render(<CouplingExplorer data={odd} initialFocus="weird|name.py" />);
    expect(screen.getByText(/Tracing/i)).toHaveTextContent("Tracing weird|name.py");
  });
});