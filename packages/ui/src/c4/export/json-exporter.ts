import type {
  ArchitectureView,
  ArchFilters,
  Persona,
} from "../types";

const PERSONA_VISIBLE_TYPES: Record<Persona, Set<string> | null> = {
  overview: new Set(["file", "config", "document", "service", "table", "endpoint", "pipeline", "schema", "resource", "module", "concept"]),
  learn: null,
  "deep-dive": null,
};

export interface ArchitectureJsonExport {
  exportDate: string;
  persona: Persona;
  filterSummary: {
    nodeTypes: string[];
    complexities: string[];
    layerIds: string[];
    edgeCategories: string[];
  };
  projectName: string;
  projectDescription: string;
  nodes: ArchitectureView["nodes"];
  edges: ArchitectureView["edges"];
}

export function exportArchitectureJson(
  view: ArchitectureView,
  filters: ArchFilters,
  persona: Persona,
): string {
  const personaFilter = PERSONA_VISIBLE_TYPES[persona];

  const visibleNodes = view.nodes.filter((node) => {
    if (personaFilter && !personaFilter.has(node.node_type)) {
      return false;
    }
    if (filters.nodeTypes.size > 0 && !filters.nodeTypes.has(node.node_type)) {
      return false;
    }
    if (filters.complexities.size > 0 && !filters.complexities.has(node.complexity)) {
      return false;
    }
    if (filters.layerIds.size > 0) {
      const nodeLayerId = view.layers.find((l) => l.node_ids.includes(node.id))?.id;
      if (nodeLayerId && !filters.layerIds.has(nodeLayerId)) {
        return false;
      }
    }
    return true;
  });

  const visibleNodeIds = new Set(visibleNodes.map((n) => n.id));

  const visibleEdges = view.edges.filter((edge) => {
    if (!visibleNodeIds.has(edge.source) || !visibleNodeIds.has(edge.target)) {
      return false;
    }
    if (filters.edgeCategories.size > 0 && !filters.edgeCategories.has(edge.edge_type)) {
      return false;
    }
    return true;
  });

  const result: ArchitectureJsonExport = {
    exportDate: new Date().toISOString(),
    persona,
    filterSummary: {
      nodeTypes: [...filters.nodeTypes],
      complexities: [...filters.complexities],
      layerIds: [...filters.layerIds],
      edgeCategories: [...filters.edgeCategories],
    },
    projectName: view.project_name,
    projectDescription: view.project_description,
    nodes: visibleNodes,
    edges: visibleEdges,
  };

  return JSON.stringify(result, null, 2);
}
