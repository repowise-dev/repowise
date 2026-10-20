/**
 * Live System Map public surface: the map, its lens control, drawer, lens
 * results and findings; the overlay builders for each lens; the AI prompt
 * builders and the pure model helpers behind them; layout, registries and
 * overlay types. The rail panels stay exported for hosts that still use them.
 */

export {
  SystemMap,
  SYSTEM_MAP_CANVAS_HEIGHT,
  SYSTEM_MAP_MINIMAP_MIN_NODES,
  type SystemMapProps,
} from "./system-map";
export { SystemMapLegend, type SystemMapLegendProps } from "./system-map-legend";
export {
  SystemMapFilters,
  Segmented,
  type SystemMapFiltersProps,
  type SegmentOption,
} from "./system-map-filters";
export { SystemMapLensControl, type SystemMapLens, type SystemMapLensControlProps } from "./system-map-lens";
export {
  SystemMapDrawer,
  selectionRepo,
  cyclePrompt,
  type ContractRef,
  type SystemMapDrawerData,
  type SystemMapDrawerProps,
  type SystemMapPromptState,
} from "./system-map-drawer";
export { SystemMapLensResults, type SystemMapLensResultsProps } from "./system-map-lens-results";
export { SystemMapFindings, type SystemMapFindingsProps } from "./system-map-findings";
export {
  buildServiceAiPrompt,
  buildEdgeAiPrompt,
  buildCycleAiPrompt,
  buildBlastRadiusAiPrompt,
  SYSTEM_MAP_PROMPT_MAX_ROWS,
} from "./system-map-ai-prompt";
export {
  healthMark,
  serviceResolver,
  summarizeServiceContracts,
  serviceDiagnostics,
  edgeLinks,
  edgeSentence,
  weightLabel,
  weightShort,
  plural,
  unmatchedReasonList,
  resolveViewSelection,
  type SystemMapContract,
  type SystemMapRepoContracts,
} from "./system-map-model";
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
export {
  applyView,
  applyCollapse,
  computeSystemMapPositions,
  layoutSignature,
  SYSTEM_MAP_NODE_SIZE,
  SYSTEM_MAP_MAX_LAYOUT_NODES,
  type SystemMapView,
  type SystemMapPositions,
} from "./layout";
export {
  SystemMapRailPanel,
  RailChip,
  RailEyebrow,
  type SystemMapRailPanelProps,
} from "./system-map-rail";
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
