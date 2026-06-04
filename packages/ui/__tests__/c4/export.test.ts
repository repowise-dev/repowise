import { describe, it, expect } from "vitest";
import type { Node, Edge } from "@xyflow/react";
import { buildC4Svg } from "../../src/c4/export/svg-exporter";
import { exportArchitectureJson } from "../../src/c4/export/json-exporter";
import { TONE_STYLES } from "../../src/graph-primitives/tone-styles";
import type { ArchitectureView, ArchFilters, ArchNode, ArchEdge } from "../../src/c4/types";

function makeArchFileNode(id: string, nodeType: string, overrides?: Partial<Node>): Node {
  return {
    id,
    type: "archFile",
    position: { x: 0, y: 0 },
    data: {
      node: {
        node_type: nodeType,
        name: `${id}.ts`,
        summary: "A test file",
        complexity: "simple",
        language: "typescript",
        in_degree: 1,
        out_degree: 2,
        is_entry_point: false,
        is_hotspot: false,
        is_dead: false,
        has_doc: false,
      },
    },
    ...overrides,
  };
}

function makeLayerClusterNode(id: string): Node {
  return {
    id,
    type: "layerCluster",
    position: { x: 0, y: 200 },
    data: {
      layer: {
        name: "API Layer",
        description: "Handles requests",
        file_count: 10,
        health_score: 85,
      },
    },
  };
}

function makePortalNode(id: string): Node {
  return {
    id,
    type: "portal",
    position: { x: 0, y: 400 },
    data: {
      targetLayerId: "layer:core",
      targetLayerName: "Core Layer",
      edgeCount: 5,
    },
  };
}

function makeArchContainerNode(id: string): Node {
  return {
    id,
    type: "archContainer",
    position: { x: 300, y: 0 },
    data: {
      containerId: id,
      label: "src/api",
      childCount: 7,
      expanded: false,
    },
  };
}

function makeC4SystemNode(id: string): Node {
  return {
    id,
    type: "system",
    position: { x: 0, y: 0 },
    data: {
      kind: "system",
      system: { id, name: "My System", description: "The main system" },
    },
  };
}

function makeArchView(overrides?: Partial<ArchitectureView>): ArchitectureView {
  const nodes: ArchNode[] = [
    {
      id: "file1", node_type: "file", name: "app.ts", file_path: "src/app.ts",
      line_range: null, summary: "Main app", complexity: "simple", tags: [],
      language: "typescript", pagerank: 0.5, pagerank_percentile: 90,
      betweenness: 0.3, in_degree: 2, out_degree: 3, community_id: 1,
      is_entry_point: true, is_test: false, is_hotspot: false, is_dead: false,
      has_doc: true, primary_owner: "dev1", primary_owner_pct: 0.8, bus_factor: 2,
    },
    {
      id: "func1", node_type: "function", name: "handleRequest", file_path: "src/handler.ts",
      line_range: [10, 50], summary: "Handles HTTP", complexity: "moderate", tags: [],
      language: "typescript", pagerank: 0.3, pagerank_percentile: 70,
      betweenness: 0.1, in_degree: 1, out_degree: 1, community_id: 1,
      is_entry_point: false, is_test: false, is_hotspot: false, is_dead: false,
      has_doc: false, primary_owner: "dev2", primary_owner_pct: 0.6, bus_factor: 1,
    },
    {
      id: "class1", node_type: "class", name: "UserService", file_path: "src/user.ts",
      line_range: [1, 100], summary: "User management", complexity: "complex", tags: [],
      language: "typescript", pagerank: 0.4, pagerank_percentile: 80,
      betweenness: 0.2, in_degree: 3, out_degree: 2, community_id: 2,
      is_entry_point: false, is_test: false, is_hotspot: true, is_dead: false,
      has_doc: true, primary_owner: "dev1", primary_owner_pct: 0.9, bus_factor: 1,
    },
  ];
  const edges: ArchEdge[] = [
    { source: "file1", target: "func1", edge_type: "imports", direction: "forward", weight: 1, confidence: 1 },
    { source: "func1", target: "class1", edge_type: "calls", direction: "forward", weight: 1, confidence: 0.9 },
  ];
  return {
    project_name: "test-project",
    project_description: "A test project",
    layers: [
      {
        id: "layer:api", name: "API", description: "API layer",
        node_ids: ["file1", "func1"], file_count: 2,
        complexity_distribution: { simple: 1, moderate: 1, complex: 0 },
        health_score: 90,
        sub_groups: [], display_order: 0,
      },
      {
        id: "layer:core", name: "Core", description: "Core layer",
        node_ids: ["class1"], file_count: 1,
        complexity_distribution: { simple: 0, moderate: 0, complex: 1 },
        health_score: 70,
        sub_groups: [], display_order: 1,
      },
    ],
    nodes,
    edges,
    tour: [],
    total_files: 3,
    total_symbols: 10,
    total_edges: 2,
    languages: ["typescript"],
    frameworks: [],
    external_systems: [],
    entry_points: [],
    entry_candidates: [],
    ...overrides,
  };
}

function makeAllFilters(view: ArchitectureView): ArchFilters {
  return {
    nodeTypes: new Set(view.nodes.map((n) => n.node_type)),
    complexities: new Set(view.nodes.map((n) => n.complexity)),
    layerIds: new Set(view.layers.map((l) => l.id)),
    edgeCategories: new Set(view.edges.map((e) => e.edge_type)),
  };
}

describe("SVG export - new node types", () => {
  it("test_svg_export_new_node_types: SVG contains rect elements for new node types", () => {
    const nodes: Node[] = [
      makeArchFileNode("f1", "file"),
      makeLayerClusterNode("lc1"),
      makePortalNode("p1"),
      makeArchContainerNode("ac1"),
    ];
    const svg = buildC4Svg(nodes, []);

    expect(svg).toContain("FILE");
    expect(svg).toContain("f1.ts");
    expect(svg).toContain("LAYER");
    expect(svg).toContain("API Layer");
    expect(svg).toContain("PORTAL");
    expect(svg).toContain("Core Layer");
    expect(svg).toContain("src/api");
    expect(svg).toContain("7 files");
  });

  it("test_svg_export_ink_palette: arch nodes render blueprint ink (kg-ux B7)", () => {
    const nodes: Node[] = [
      makeArchFileNode("f1", "file"),
      makeLayerClusterNode("lc1"),
      makePortalNode("p1"),
    ];
    const svg = buildC4Svg(nodes, []);

    // Headless export resolves the light-mode fallbacks of the kg tokens.
    expect(svg).toContain("#fffdf8"); // --color-kg-node-fill (paper card)
    expect(svg).toContain("#241b2c"); // --color-kg-node-text / ink outline
    expect(svg).toContain("#826aa0"); // --color-diagram-cluster-border (ghost portal)
    expect(svg).toContain("#f4eae1"); // warm paper canvas
    expect(svg).toContain("kg-grid"); // graph-paper pattern
    expect(svg).toContain('stroke-dasharray="8 5"'); // dashed ghost boundary
  });

  it("test_svg_export_entry_accent: entry points render the ember gradient", () => {
    const entry = makeArchFileNode("e1", "file");
    (entry.data as { node: { is_entry_point: boolean } }).node.is_entry_point = true;
    const svg = buildC4Svg([entry], []);
    expect(svg).toContain("url(#kg-ember)");
    expect(svg).toContain("#f59520");
  });

  it("test_svg_export_backward_compat: existing C4 node types still render", () => {
    const nodes: Node[] = [makeC4SystemNode("sys1")];
    const edges: Edge[] = [];
    const svg = buildC4Svg(nodes, edges);

    expect(svg).toContain("SYSTEM");
    expect(svg).toContain("My System");
    expect(svg).toContain(TONE_STYLES.system.bg);
    expect(svg).toContain(TONE_STYLES.system.border);
  });
});

describe("JSON export", () => {
  it("test_json_export_filtered: filtered-out nodes excluded from JSON", () => {
    const view = makeArchView();
    const filters: ArchFilters = {
      nodeTypes: new Set(["file"]),
      complexities: new Set(["simple", "moderate", "complex"]),
      layerIds: new Set(view.layers.map((l) => l.id)),
      edgeCategories: new Set(view.edges.map((e) => e.edge_type)),
    };

    const json = exportArchitectureJson(view, filters, "deep-dive");
    const parsed = JSON.parse(json);

    expect(parsed.nodes).toHaveLength(1);
    expect(parsed.nodes[0].id).toBe("file1");
    expect(parsed.edges).toHaveLength(0);
  });

  it("test_json_export_persona: overview persona excludes function/class", () => {
    const view = makeArchView();
    const filters = makeAllFilters(view);

    const json = exportArchitectureJson(view, filters, "overview");
    const parsed = JSON.parse(json);

    const nodeTypes = parsed.nodes.map((n: ArchNode) => n.node_type);
    expect(nodeTypes).not.toContain("function");
    expect(nodeTypes).not.toContain("class");
    expect(nodeTypes).toContain("file");

    expect(parsed.persona).toBe("overview");
    expect(parsed.exportDate).toBeDefined();
    expect(parsed.projectName).toBe("test-project");
  });
});

describe("Export menu options", () => {
  it("test_export_menu_all_options: menu shows SVG, PNG, Mermaid, JSON options", async () => {
    expect(typeof buildC4Svg).toBe("function");
    expect(typeof exportArchitectureJson).toBe("function");

    const { C4ExportMenu } = await import("../../src/c4/export/ExportMenu");
    expect(C4ExportMenu).toBeDefined();

    const { downloadPng } = await import("../../src/c4/export/png-exporter");
    expect(typeof downloadPng).toBe("function");
  });
});
