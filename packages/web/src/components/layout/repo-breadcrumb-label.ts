const SEGMENT_LABELS: Record<string, string> = {
  overview: "Overview",
  docs: "Docs",
  coverage: "Coverage",
  search: "Search",
  graph: "Graph",
  symbols: "Symbols",
  ownership: "Ownership",
  hotspots: "Hotspots",
  "dead-code": "Dead Code",
  "blast-radius": "Blast Radius",
  decisions: "Decisions",
  costs: "Costs",
  risk: "Risk",
  security: "Security",
  settings: "Settings",
  c4: "Knowledge Graph",
};

export function getRepoBreadcrumbSegmentLabel(segment: string): string {
  if (SEGMENT_LABELS[segment]) return SEGMENT_LABELS[segment];

  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}
