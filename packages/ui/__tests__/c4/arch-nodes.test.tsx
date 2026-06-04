import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "../../src/graph-primitives/node-shell";
import { ArchFileNode } from "../../src/c4/nodes/ArchFileNode";
import type { ArchNode, ArchLayer } from "../../src/c4/types";

vi.mock("@xyflow/react", () => ({
  Handle: () => null,
  Position: { Top: "top", Bottom: "bottom" },
}));

describe("NodeShell", () => {
  it("renders selected state with golden border", () => {
    const { container } = render(
      <NodeShell tone="file" kindLabel="FILE" title="test.py" selected={true} />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.border).toContain("rgb(251, 191, 36)");
  });

  it("renders tour highlight with accentPulse animation", () => {
    const { container } = render(
      <NodeShell tone="file" kindLabel="FILE" title="test.py" tourHighlight={true} />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.animation).toContain("accentPulse");
  });

  it("renders diff faded state with opacity 0.25", () => {
    const { container } = render(
      <NodeShell tone="file" kindLabel="FILE" title="test.py" diffState="faded" />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.opacity).toBe("0.25");
  });

  it("renders diff changed state with red border", () => {
    const { container } = render(
      <NodeShell tone="file" kindLabel="FILE" title="test.py" diffState="changed" />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.border).toContain("rgb(252, 165, 165)");
  });

  it("renders search highlight with dashed border", () => {
    const { container } = render(
      <NodeShell tone="file" kindLabel="FILE" title="test.py" searchHighlight={true} />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.border).toContain("dashed");
  });

  it("renders docs badge when hasDocs is true", () => {
    const { getByLabelText } = render(
      <NodeShell tone="file" kindLabel="FILE" title="test.py" hasDocs={true} />,
    );
    expect(getByLabelText("Documentation available")).toBeDefined();
  });
});

const mockNode: ArchNode = {
  id: "src/app.py",
  node_type: "file",
  name: "app.py",
  file_path: "src/app.py",
  line_range: null,
  summary: "Main application entry point",
  complexity: "simple",
  tags: ["python"],
  language: "python",
  pagerank: 0.5,
  pagerank_percentile: 90,
  betweenness: 0.3,
  in_degree: 2,
  out_degree: 3,
  community_id: 1,
  is_entry_point: true,
  is_test: false,
  is_hotspot: true,
  is_dead: false,
  has_doc: true,
  primary_owner: "dev1",
  primary_owner_pct: 0.75,
  bus_factor: 2,
};

describe("ArchFileNode (via NodeShell)", () => {
  it("renders file node with name, type badge, summary, and complexity dot", () => {
    const { getByText } = render(
      <NodeShell
        tone={mockNode.node_type}
        kindLabel={`${mockNode.node_type.toUpperCase()} · ${mockNode.language}`}
        title={mockNode.name}
        subtitle={mockNode.summary}
        footer={<span>↓{mockNode.in_degree} ↑{mockNode.out_degree}</span>}
      />,
    );
    expect(getByText("app.py")).toBeDefined();
    expect(getByText("FILE · python")).toBeDefined();
    expect(getByText("Main application entry point")).toBeDefined();
    expect(getByText("↓2 ↑3")).toBeDefined();
  });
});

describe("ArchFileNode barrels + tour badge (plan C-2/C-3)", () => {
  function renderArchFile(overrides: Partial<ArchNode>, dataExtra: object = {}) {
    const node = { ...mockNode, ...overrides };
    const props = {
      data: { node, ...dataExtra },
      selected: false,
    } as unknown as NodeProps;
    return render(<ArchFileNode {...props} />);
  }

  it("de-emphasizes barrel-tagged nodes and suppresses their entry badge", () => {
    const { container, queryByLabelText, getByText } = renderArchFile({
      tags: ["barrel", "typescript"],
      is_entry_point: true,
      summary: "Re-export barrel for ui/.",
    });
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.style.opacity).toBe("0.55");
    expect(queryByLabelText("Entry point")).toBeNull();
    expect(getByText("barrel")).toBeDefined(); // honest kind label
    expect(getByText("Re-export barrel for ui/.")).toBeDefined(); // honest summary
  });

  it("keeps the entry badge for genuine entry points", () => {
    const { getByLabelText, container } = renderArchFile({ is_entry_point: true });
    expect(getByLabelText("Entry point")).toBeDefined();
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.style.opacity).not.toBe("0.55");
  });

  it("shows the numbered tour-step badge when highlighted by the tour", () => {
    const { getByLabelText } = renderArchFile({}, { tourStepNumber: 3, tourHighlight: true });
    expect(getByLabelText("Tour step 3")).toBeDefined();
  });
});

describe("LayerClusterNode (via NodeShell)", () => {
  const mockLayer: ArchLayer = {
    id: "layer:api",
    name: "API Layer",
    description: "Handles HTTP requests",
    node_ids: ["a", "b", "c"],
    file_count: 42,
    complexity_distribution: { simple: 20, moderate: 15, complex: 7 },
    health_score: 85,
    sub_groups: [],
    display_order: 0,
  };

  it("renders layer cluster with name, file count, and health score", () => {
    const { getByText } = render(
      <NodeShell
        tone="layerCluster"
        kindLabel="LAYER"
        title={mockLayer.name}
        subtitle={mockLayer.description}
        footer={<span>{mockLayer.file_count} files</span>}
      />,
    );
    expect(getByText("API Layer")).toBeDefined();
    expect(getByText("LAYER")).toBeDefined();
    expect(getByText("Handles HTTP requests")).toBeDefined();
    expect(getByText("42 files")).toBeDefined();
  });
});

describe("PortalNode (via NodeShell)", () => {
  it("renders portal node with target layer name and connection count", () => {
    const { getByText } = render(
      <NodeShell
        tone="portal"
        kindLabel="PORTAL"
        title="→ Core Layer"
        subtitle="5 connections"
      />,
    );
    expect(getByText("PORTAL")).toBeDefined();
    expect(getByText("→ Core Layer")).toBeDefined();
    expect(getByText("5 connections")).toBeDefined();
  });
});
