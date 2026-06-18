const SEGMENT_LABELS: Record<string, string> = {
  overview: "Overview",
  docs: "Docs",
  chat: "Chat",
  architecture: "Architecture",
  "code-health": "Code Health",
  coverage: "Coverage",
  "refactoring-targets": "Refactoring Targets",
  trend: "Trend",
  search: "Search",
  graph: "Graph",
  symbols: "Symbols",
  ownership: "Ownership",
  hotspots: "Hotspots",
  "dead-code": "Dead Code",
  "blast-radius": "Blast Radius",
  decisions: "Decisions",
  commits: "Commits",
  owners: "Contributors",
  modules: "Modules",
  wiki: "Wiki",
  health: "Code Health",
  costs: "Usage & Savings",
  risk: "Risk",
  security: "Security",
  settings: "Settings",
  "knowledge-graph": "Knowledge Graph",
  files: "Files",
};

export function getRepoBreadcrumbSegmentLabel(segment: string): string {
  if (SEGMENT_LABELS[segment]) return SEGMENT_LABELS[segment];

  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}
