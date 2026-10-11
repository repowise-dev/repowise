import type { ArchitectureView } from "../../src/c4/types";

export function createMockView(overrides?: Partial<ArchitectureView>): ArchitectureView {
  return {
    project_name: "test-project",
    project_description: "A test project",
    layers: [
      { id: "layer:api", name: "API", description: "API layer", node_ids: ["src/app.py", "src/routes.py"], file_count: 2, complexity_distribution: { simple: 1, moderate: 1, complex: 0 }, health_score: 85, sub_groups: [], display_order: 0 },
      { id: "layer:core", name: "Core", description: "Core logic", node_ids: ["src/models.py"], file_count: 1, complexity_distribution: { simple: 0, moderate: 0, complex: 1 }, health_score: 72, sub_groups: [], display_order: 1 },
    ],
    nodes: [
      { id: "src/app.py", node_type: "file", name: "app.py", file_path: "src/app.py", line_range: null, summary: "Main application entry point", complexity: "simple", tags: ["python", "entry"], language: "python", pagerank: 0.5, pagerank_percentile: 90, betweenness: 0.3, in_degree: 2, out_degree: 3, community_id: 1, is_entry_point: true, is_test: false, is_hotspot: false, is_dead: false, has_doc: true, primary_owner: "dev1", primary_owner_pct: 0.75, bus_factor: 2 },
      { id: "src/routes.py", node_type: "file", name: "routes.py", file_path: "src/routes.py", line_range: null, summary: "Route handlers", complexity: "moderate", tags: ["python", "src"], language: "python", pagerank: 0.3, pagerank_percentile: 60, betweenness: 0.1, in_degree: 1, out_degree: 2, community_id: 1, is_entry_point: false, is_test: false, is_hotspot: true, is_dead: false, has_doc: false, primary_owner: "dev2", primary_owner_pct: 0.5, bus_factor: 3 },
      { id: "src/models.py", node_type: "file", name: "models.py", file_path: "src/models.py", line_range: null, summary: "Data models", complexity: "complex", tags: ["python", "core"], language: "python", pagerank: 0.8, pagerank_percentile: 95, betweenness: 0.5, in_degree: 5, out_degree: 1, community_id: 2, is_entry_point: false, is_test: false, is_hotspot: false, is_dead: false, has_doc: true, primary_owner: "dev1", primary_owner_pct: 0.9, bus_factor: 1 },
    ],
    edges: [
      { source: "src/app.py", target: "src/routes.py", edge_type: "imports", direction: "forward", weight: 1, confidence: 1 },
      { source: "src/app.py", target: "src/models.py", edge_type: "imports", direction: "forward", weight: 1, confidence: 1 },
      { source: "src/routes.py", target: "src/models.py", edge_type: "calls", direction: "forward", weight: 0.8, confidence: 0.9 },
    ],
    tour: [
      { order: 1, title: "Entry Point", description: "Start here", node_ids: ["src/app.py"], target_path: null, layer_id: null, reason: "", depth: null, kind: "" as const, page_type: null },
      { order: 2, title: "Routes", description: "HTTP handlers", node_ids: ["src/routes.py"], target_path: null, layer_id: null, reason: "", depth: null, kind: "" as const, page_type: null },
      { order: 3, title: "Models", description: "Data layer", node_ids: ["src/models.py"], target_path: null, layer_id: null, reason: "", depth: null, kind: "" as const, page_type: null },
    ],
    total_files: 3,
    total_symbols: 25,
    total_edges: 3,
    languages: ["python"],
    frameworks: ["fastapi"],
    external_systems: [],
    entry_points: [],
    entry_candidates: [],
    ...overrides,
  };
}
