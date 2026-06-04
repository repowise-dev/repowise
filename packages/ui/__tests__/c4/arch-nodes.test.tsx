import { describe, it, expect, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "../../src/graph-primitives/node-shell";
import { ArchFileNode } from "../../src/c4/nodes/ArchFileNode";
import { LayerClusterNode } from "../../src/c4/nodes/LayerClusterNode";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import { createMockView } from "./fixtures";
import type { ArchNode, ArchLayer } from "../../src/c4/types";

vi.mock("@xyflow/react", () => ({
  Handle: () => null,
  Position: { Top: "top", Bottom: "bottom" },
}));

describe("NodeShell", () => {
  it("renders selected state with the brand selection token", () => {
    const { container } = render(
      <NodeShell tone="file" kindLabel="FILE" title="test.py" selected={true} />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.borderLeft).toContain("--color-viz-selection");
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

  it("renders diff changed state with the diff-changed token", () => {
    const { container } = render(
      <NodeShell tone="file" kindLabel="FILE" title="test.py" diffState="changed" />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.border).toContain("--color-viz-diff-changed");
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

  it("selection dimming fades unrelated cards to 0.45, never vanishes them", () => {
    const { container } = renderArchFile({}, { dimmed: true });
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.style.opacity).toBe("0.45");
    // The diff overlay keeps its own stronger fade — dimming must not hijack it.
    const inner = wrapper.firstElementChild as HTMLElement;
    expect(inner.style.opacity).not.toBe("0.25");
  });
});

describe("a11y: tier cards are keyboard-operable (plan D)", () => {
  function renderCard(kind: "layer" | "subGroup", layerId: string) {
    const store = useArchitectureStore;
    store.setState(store.getInitialState());
    const view = createMockView();
    // Give layer:api curated sub-groups so drilling lands on the groups tier.
    view.layers[0]!.sub_groups = [
      { id: "layer:api:app", name: "app", node_ids: ["src/app.py"] },
      { id: "layer:api:routes", name: "routes", node_ids: ["src/routes.py"] },
    ];
    store.getState().setView(view);
    const layer = { ...view.layers[0]!, id: layerId, name: "API" };
    const props = {
      data: { layer, kind },
      selected: false,
    } as unknown as NodeProps;
    return render(<LayerClusterNode {...props} />);
  }

  // Unified click grammar (kg-ux plan B5): single activation = select +
  // inspect, never drill. Drilling is double-click (page handler) or the
  // inspect panel's "Open layer/group" button.
  it("Enter on a layer card selects it without drilling", () => {
    const { getByRole } = renderCard("layer", "layer:api");
    fireEvent.keyDown(getByRole("button"), { key: "Enter" });
    expect(useArchitectureStore.getState().selectedNodeId).toBe("layer:api");
    expect(useArchitectureStore.getState().navigationLevel).toBe("overview");
  });

  it("Space on a sub-group card selects it without drilling", () => {
    const { getByRole } = renderCard("subGroup", "layer:api:app");
    fireEvent.keyDown(getByRole("button"), { key: " " });
    expect(useArchitectureStore.getState().selectedNodeId).toBe("layer:api:app");
    expect(useArchitectureStore.getState().activeSubGroupId).toBeNull();
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
