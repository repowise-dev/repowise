/**
 * Live System Map — public surface. The host renders `<SystemMap>` with a
 * `SystemGraph`; everything else (registries, layout, overlay types) is
 * exported for downstream consumers (hosted frontend) and later phases.
 */

export { SystemMap, type SystemMapProps } from "./system-map";
export { SystemMapLegend } from "./system-map-legend";
export { SystemMapFilters, type SystemMapFiltersProps } from "./system-map-filters";
export { SystemMapInspector, type SystemMapInspectorProps } from "./system-map-inspector";
export { SystemMapBlastPanel, type SystemMapBlastPanelProps } from "./system-map-blast-panel";
export { buildBlastRadiusOverlay, impactBadgeTone } from "./blast-radius";
export {
  SystemMapBreakingPanel,
  type SystemMapBreakingPanelProps,
} from "./system-map-breaking-panel";
export { buildBreakingChangeOverlay } from "./breaking-changes";
export {
  SystemMapConformancePanel,
  type SystemMapConformancePanelProps,
} from "./system-map-conformance-panel";
export { buildConformanceOverlay } from "./conformance";
export {
  buildArchitectureOverlay,
  roleStyle,
  ROLE_STYLE,
  ROLE_ORDER,
  type RoleStyle,
} from "./architecture";
export { useSystemMapLayout, type SystemMapLayout, type UseSystemMapLayoutArgs } from "./use-system-map-layout";
export { applyView, computeSystemMapPositions, SYSTEM_MAP_NODE_SIZE, type SystemMapView } from "./layout";
export { collapseToRepos } from "./collapse";
export {
  SYSTEM_EDGE_KINDS,
  EDGE_KIND_ORDER,
  edgeKindStyle,
  matchTypeDash,
  matchTypeLabel,
  type SystemEdgeKindStyle,
  type EdgeCategory,
} from "./edge-kinds";
export { SYSTEM_NODE_KINDS, NODE_KIND_ORDER, nodeKindStyle, type SystemNodeKindStyle } from "./node-kinds";
export {
  resolveNodeOverlay,
  resolveEdgeOverlay,
  type RepoHealth,
  type SystemMapOverlay,
  type SystemMapBadge,
  type SystemMapSelection,
  type NodeOverlayState,
  type EdgeOverlayState,
} from "./types";
