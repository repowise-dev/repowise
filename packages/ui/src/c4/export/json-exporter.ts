import type {
  ArchitectureView,
  ArchFilters,
  Persona,
} from "../types";
import { PERSONA_NODE_TYPES } from "../types";

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
  const personaFilter = PERSONA_NODE_TYPES[persona];

  const nodeLayerMap = new Map<string, string>();
  for (const layer of view.layers) {
    for (const nid of layer.node_ids) {
      nodeLayerMap.set(nid, layer.id);
    }
  }

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
      const nodeLayerId = nodeLayerMap.get(node.id);
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
