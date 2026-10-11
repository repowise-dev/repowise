const SEGMENT_LABELS: Record<string, string> = {
  overview: "Overview",
  docs: "Docs",
  chat: "Chat",
  architecture: "Architecture",
  "code-health": "Code Health",
  coverage: "Tests",
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
  zoom: "Zoom Map",
  files: "Files",
};

/**
 * Message keys under the `breadcrumb` namespace for the configured segments.
 *
 * Kept beside the English labels so the two cannot drift: every key here has
 * a matching entry in `messages/en.json` under `breadcrumb`, and a segment
 * with no key (a dynamic id, an unknown route) falls back to the decoded
 * segment itself.
 */
export const SEGMENT_MESSAGE_KEYS: Record<string, string> = {
  overview: "overview",
  docs: "docs",
  chat: "chat",
  architecture: "architecture",
  "code-health": "codeHealth",
  coverage: "coverage",
  "refactoring-targets": "refactoringTargets",
  trend: "trend",
  search: "search",
  graph: "graph",
  symbols: "symbols",
  ownership: "ownership",
  hotspots: "hotspots",
  "dead-code": "deadCode",
  "blast-radius": "blastRadius",
  decisions: "decisions",
  commits: "commits",
  owners: "owners",
  modules: "modules",
  wiki: "wiki",
  health: "health",
  costs: "costs",
  risk: "risk",
  security: "security",
  settings: "settings",
  "knowledge-graph": "knowledgeGraph",
  zoom: "zoom",
  files: "files",
};

export function getRepoBreadcrumbSegmentLabel(segment: string): string {
  if (SEGMENT_LABELS[segment]) return SEGMENT_LABELS[segment];

  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}
