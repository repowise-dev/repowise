/**
 * Live System Map public surface: the map, its lens control, drawer, lens
 * results and findings; the overlay builders for each lens; the AI prompt
 * builders and the pure model helpers behind them; the levels model (groups,
 * bands, focus columns, paths between two services, draw budget); layout,
 * registries and overlay types. The rail panels stay exported for hosts that
 * still use them.
 */

export { SystemMap, SYSTEM_MAP_CANVAS_HEIGHT, type SystemMapProps } from "./system-map";
export { SystemMapCanvas, SYSTEM_MAP_MINIMAP_MIN_NODES, type SystemMapCanvasProps } from "./system-map-canvas";
export { SystemMapGroupNode } from "./system-map-group-node";
export { SystemMapTrayNode, SystemMapMoreNode } from "./system-map-tray-node";
export { SystemMapBand, BAND_STROKE, bandWidth } from "./system-map-band";
export { SystemMapLensBadge } from "./system-map-lens-badge";
export { SystemMapBreadcrumb, breadcrumbTrail, type SystemMapBreadcrumbProps } from "./system-map-breadcrumb";
export { GroupPanel, BandPanel, bandPairs } from "./system-map-aggregate-panel";
export { SystemMapActionsContext, useSystemMapActions, type SystemMapActions } from "./system-map-actions";
export { useSystemMapState, type SystemMapState, type SystemMapStateArgs } from "./use-system-map-state";
export {
  ESTATE_VIEW,
  MAP_VIEW_PARAMS,
  TRAY_OPEN_TOKEN,
  parseMapView,
  writeMapView,
  drawnFamilies,
  toLevelViewState,
  openContainer,
  closeContainer,
  toggleFamily,
  showFamily,
  backOut,
  toEstate,
  sameMapView,
  focusService,
  compareServices,
  withDisplay,
  type MapViewState,
  type MapDisplay,
} from "./view-state";
export { SystemMapSearch, type SystemMapSearchProps } from "./system-map-search";
export { useSystemMapSearch, type MapSearch } from "./use-system-map-search";
export {
  buildSearchIndex,
  searchMap,
  routeHits,
  pickToView,
  SEARCH_LIMIT,
  SEARCH_MIN_CHARS,
  type SearchHit,
  type MapSearchIndex,
  type SystemMapRouteLink,
  type SystemMapRouteLoader,
} from "./search-index";
export { shortcutFor, shortcutHelpRows, type MapShortcut, type ShortcutKey } from "./shortcuts";
export { useMapNavigation, type MapNavigation } from "./use-map-navigation";
export { SystemMapList, type SystemMapListProps } from "./system-map-list";
export {
  serviceRows,
  sortServiceRows,
  nextSort,
  DEFAULT_SERVICE_SORT,
  type ServiceRow,
  type ServiceSort,
  type ServiceSortKey,
} from "./service-rows";
export { SystemMapHealthMark } from "./system-map-health-mark";
export { levelAnnouncement, type AnnounceArgs } from "./announce";
export { DEFAULT_NAV_COPY, navCopyWith, type SystemMapNavCopy } from "./nav-copy";
export { resolveSelection, type ResolvedSelection } from "./selection-fix";
export { scopeCaption, hiddenFamiliesSentence, truncationSentence } from "./scope-caption";
export {
  toFlowElements,
  drawnLabel,
  type FlowInputs,
  type FlowElements,
  type GroupNodeData,
  type TrayNodeData,
  type MoreNodeData,
  type BandEdgeData,
} from "./flow-elements";
export {
  groupSourceLabel,
  healthCounts,
  healthExceptions,
  healthSummaryText,
  kindMixText,
  bandSentence,
  type HealthCounts,
  type HealthException,
} from "./aggregate-facts";
export { SystemMapLegend, type SystemMapLegendProps } from "./system-map-legend";
export { SystemMapEvidenceNotice, type SystemMapEvidenceNoticeProps } from "./system-map-evidence-notice";
export {
  SystemMapFilters,
  Segmented,
  type SystemMapFiltersProps,
  type SegmentOption,
} from "./system-map-filters";
export { SystemMapLensControl, type SystemMapLens, type SystemMapLensControlProps } from "./system-map-lens";
export {
  SystemMapDrawer,
  SystemMapRailFrame,
  drawerHasSubject,
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
  serviceResolver,
  summarizeServiceContracts,
  serviceDiagnostics,
  edgeSentence,
  weightLabel,
  weightShort,
  plural,
  unmatchedReasonList,
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
export { useLevelLayout, type LevelLayout, type UseLevelLayoutArgs } from "./use-level-layout";
export { LEVEL_NODE_SIZES, CONTAINER_PADDING, SYSTEM_MAP_NODE_SIZE, type LevelNodeSizes } from "./layout-scopes";
export type { SystemMapPhase } from "./use-system-map-phases";
export {
  SystemMapRailPanel,
  RailChip,
  RailEyebrow,
  type SystemMapRailPanelProps,
} from "./system-map-rail";
export {
  SYSTEM_EDGE_KINDS,
  EDGE_KIND_ORDER,
  edgeKindStyle,
  matchTypeDash,
  matchTypeLabel,
  type SystemEdgeKindStyle,
  type EdgeCategory,
} from "./edge-kinds";
export {
  buildLevelView,
  normalizeGraph,
  type LevelView,
} from "./levels";
export {
  buildEstateView,
  activeGroups,
  linkedWeights,
  groupNodeId,
  OPEN_GROUP_LIMIT,
  TRAY_NODE_ID,
  ESTATE_MORE_ID,
  type EstateView,
} from "./estate-view";
export {
  focusColumns,
  placeFocusColumns,
  focusNodeId,
  focusMoreId,
  type FocusColumn,
  type FocusConnector,
  type FocusEntry,
  type FocusLayout,
  type FocusPlacement,
} from "./focus-columns";
export {
  servicesBetween,
  shortestPaths,
  buildAdjacency,
  type BetweenOptions,
  type BetweenResult,
  type PathHop,
  type ServicePath,
} from "./between";
export { matchServices, pickerPath, resolveService } from "./between-picker";
export { focusColumnLabels, FOCUS_COLUMN_TITLES, type FocusColumnLabel } from "./focus-labels";
export {
  ROUTES_PAGE,
  planRoutes,
  routesLede,
  hiddenRoutesSentence,
  type LinksBetweenOptions,
  type LoadLinksBetween,
  type RoutesPlan,
} from "./routes-between";
export { SystemMapRoutesPanel, type SystemMapRoutesPanelProps } from "./system-map-routes-panel";
export { SystemMapBetweenControl, type SystemMapBetweenControlProps } from "./system-map-between-control";
export { RouteRow } from "./system-map-route-row";
export { buildPairAiPrompt, type BuildPairPromptOptions } from "./system-map-pair-prompt";
export { useRoutesBetween, useEdgeRoutes, type RoutesBetweenState, type EdgeRoutes } from "./use-routes-between";
export { connectEdges, summarizeLinks, bandId, compareIds } from "./bands";
export { DRAW_BUDGET, allocateSlots, capEdges, truncationReport, type BudgetContainer } from "./draw-budget";
export {
  EDGE_FAMILY_ORDER,
  EDGE_FAMILY_COPY,
  kindCountsByFamily,
  DEFAULT_EDGE_FAMILIES,
  LINKED_FAMILIES,
  edgeFamily,
  edgeFamilyOf,
  familyCounts,
  familyTally,
  linkWeight,
  type EdgeFamily,
  type FamilyCount,
  type FamilyTally,
} from "./edge-families";
export type {
  DrawBudget,
  DrawnBand,
  DrawnEdge,
  DrawnGroupNode,
  DrawnMoreNode,
  DrawnNode,
  DrawnServiceNode,
  DrawnSingleEdge,
  DrawnTrayNode,
  FocusColumnId,
  LevelViewState,
  LevelsEdge,
  LevelsGraph,
  LevelsGroupMeta,
  LevelsNode,
  LinkSummary,
  MapLevel,
  TruncationReport,
} from "./level-types";
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
