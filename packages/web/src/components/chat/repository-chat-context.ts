import type {
  ChatContext,
  ChatContextKind,
  ChatContextTargetKind,
} from "@repowise-dev/ui/chat";
import { getRepoBreadcrumbSegmentLabel } from "@/components/layout/repo-breadcrumb-label";

interface SearchParamsReader {
  get(name: string): string | null;
  getAll?(name: string): string[];
}

const CONTEXT_QUERY_KEYS = [
  "page", "commit", "file", "node", "view", "package", "focus", "module", "tab", "lens",
] as const;

/** Keep workspace-only query state (conversation/artifact/compare) out of chat context identity. */
export function getRepositoryChatContextQuery(searchParams: SearchParamsReader): string {
  const relevant = new URLSearchParams();
  for (const key of CONTEXT_QUERY_KEYS) {
    const values = searchParams.getAll?.(key) ?? [searchParams.get(key)].filter((value): value is string => value !== null);
    for (const value of values) relevant.append(key, value);
  }
  return relevant.toString();
}

interface RouteDefinition {
  kind: ChatContextKind;
  targetKind?: ChatContextTargetKind;
}

const ROUTES: Readonly<Record<string, RouteDefinition>> = {
  overview: { kind: "overview" },
  docs: { kind: "documentation", targetKind: "documentation" },
  wiki: { kind: "documentation", targetKind: "documentation" },
  architecture: { kind: "architecture" },
  c4: { kind: "architecture" },
  graph: { kind: "graph" },
  "knowledge-graph": { kind: "graph" },
  zoom: { kind: "graph" },
  "code-health": { kind: "health" },
  health: { kind: "health" },
  coverage: { kind: "health" },
  refactoring: { kind: "refactoring" },
  "refactoring-targets": { kind: "refactoring" },
  files: { kind: "file", targetKind: "path" },
  symbols: { kind: "symbol", targetKind: "symbol" },
  modules: { kind: "module", targetKind: "module" },
  commits: { kind: "commit", targetKind: "commit" },
  owners: { kind: "contributor", targetKind: "person" },
  ownership: { kind: "contributor", targetKind: "person" },
  decisions: { kind: "decision", targetKind: "decision" },
  hotspots: { kind: "risk" },
  "dead-code": { kind: "risk" },
  "blast-radius": { kind: "risk" },
  risk: { kind: "risk" },
  security: { kind: "security" },
  costs: { kind: "usage" },
  settings: { kind: "settings" },
  stats: { kind: "settings" },
  chat: { kind: "chat" },
};

function decodeSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

const ARCHITECTURE_VIEW_LABELS: Readonly<Record<string, string>> = {
  communities: "Map",
  files: "Files",
  coupling: "Coupling",
  packages: "Third-party",
  symbols: "Symbols",
};

const HEALTH_TAB_LABELS: Readonly<Record<string, string>> = {
  triage: "Overview",
  performance: "Performance",
  findings: "Findings",
  coverage: "Tests",
  "dead-code": "Dead Code",
  security: "Security",
  impact: "Blast Radius",
};

const HEALTH_LENS_LABELS: Readonly<Record<string, string>> = {
  health: "Health",
  maintainability: "Maintainability",
  performance: "Performance",
  churn: "Churn",
};

function queryTargets(searchParams: SearchParamsReader | undefined, key: string): string[] {
  const raw = searchParams?.getAll?.(key) ?? [searchParams?.get(key) ?? ""];
  return [...new Set(raw.map((value) => value.trim()).filter(Boolean))];
}

function joinedTargets(searchParams: SearchParamsReader | undefined, key: string): string {
  return queryTargets(searchParams, key).slice(0, 8).join(", ").slice(0, 1800);
}

/** Convert the OSS repository router into the portable shared chat contract. */
export function getRepositoryChatContext(
  pathname: string,
  searchParams?: SearchParamsReader,
): ChatContext {
  const match = pathname.match(/^\/repos\/[^/]+(?:\/(.*))?\/?$/);
  const segments = (match?.[1] ?? "")
    .split("/")
    .filter(Boolean)
    .map(decodeSegment);
  const route = segments[0];

  if (!route) return { kind: "repository", label: "Repository" };

  if (route === "docs" && segments[1] === "coverage") {
    return { kind: "health", label: "Tests" };
  }
  if (
    (route === "code-health" || route === "health") &&
    segments[1] === "refactoring-targets"
  ) {
    return { kind: "refactoring", label: "Refactoring Targets" };
  }

  const definition = ROUTES[route] ?? { kind: "repository" as const };
  const routeTarget = segments.slice(1).join("/");
  const docsTarget = route === "docs" ? joinedTargets(searchParams, "page") : "";
  const commitTarget = route === "commits" ? joinedTargets(searchParams, "commit") : "";
  const selectedFiles =
    route === "code-health" || route === "health" || definition.kind === "risk"
      ? joinedTargets(searchParams, "file")
      : "";
  const isArchitecture =
    definition.kind === "architecture" || definition.kind === "graph";
  const architectureNode = isArchitecture ? joinedTargets(searchParams, "node") : "";
  const architectureView = isArchitecture ? searchParams?.get("view") ?? "" : "";
  const architecturePackage =
    isArchitecture && architectureView === "packages"
      ? joinedTargets(searchParams, "package")
      : "";
  const architectureFocus =
    isArchitecture &&
    (definition.kind === "graph" || architectureView === "coupling")
      ? joinedTargets(searchParams, "focus")
      : "";
  const architectureModule = isArchitecture ? joinedTargets(searchParams, "module") : "";
  const architectureTarget =
    architecturePackage || architectureNode || architectureFocus || architectureModule;
  const architectureTargetKind: ChatContextTargetKind | undefined =
    architecturePackage
      ? "dependency"
      : architectureModule && !architectureNode && !architectureFocus
      ? "module"
      : architectureTarget
        ? "path"
        : undefined;
  const target =
    docsTarget || commitTarget || selectedFiles || architectureTarget || routeTarget;
  const targetKind: ChatContextTargetKind | undefined =
    docsTarget
      ? "documentation"
      : commitTarget
        ? "commit"
        : selectedFiles
          ? "path"
          : architectureTargetKind ?? (target ? definition.targetKind : undefined);

  let label = getRepoBreadcrumbSegmentLabel(route);
  if (!target && definition.kind === "architecture") {
    const view = searchParams?.get("view") ?? "";
    const viewLabel = ARCHITECTURE_VIEW_LABELS[view];
    if (viewLabel) label = `${label} · ${viewLabel}`;
  }
  if (!target && definition.kind === "health") {
    const tab = searchParams?.get("tab") ?? "";
    const lens = searchParams?.get("lens") ?? "";
    const detailLabel = HEALTH_TAB_LABELS[tab] ?? HEALTH_LENS_LABELS[lens];
    if (detailLabel) label = `${label} · ${detailLabel}`;
  }

  return {
    kind: definition.kind,
    label,
    ...(target ? { target } : {}),
    ...(targetKind ? { targetKind } : {}),
  };
}
