/**
 * @repowise-dev/ui/c4 — shared C4 diagram surface.
 *
 * Public entry point. Host pages compose `<C4Diagram>` with data fetched
 * from `/api/graph/{repo_id}/c4/{l1,l2,l3}` and either lift state up via
 * props OR drive it locally with `useC4Store`.
 */

export { C4Diagram } from "./C4Diagram";
export type { C4DiagramProps } from "./C4Diagram";

export { ArchCanvas } from "./ArchCanvas";
export type { ArchCanvasProps } from "./ArchCanvas";

export { useC4Store } from "./store/use-c4-store";
export type { C4Store, C4StoreState, UseC4StoreOptions } from "./store/use-c4-store";

export { useArchitectureStore } from "./store/use-architecture-store";
export type { ArchitectureStore } from "./store/use-architecture-store";

export { useC4Layout } from "./hooks/use-c4-layout";
export type { C4LayoutResult } from "./hooks/use-c4-layout";
export { useC4Keyboard } from "./hooks/use-c4-keyboard";
export { useArchitectureLayout } from "./hooks/use-architecture-layout";
export type { ArchitectureLayoutResult } from "./hooks/use-architecture-layout";
export { useArchitectureNavigation } from "./hooks/use-architecture-navigation";

export { computeC4Layout, C4_NODE_SIZES } from "./layout/elk-c4-layout";
export type {
  C4LayoutNode,
  C4LayoutEdge,
  C4LayoutPosition,
} from "./layout/elk-c4-layout";

export { c4NodeTypes, archNodeTypes } from "./nodes";
export {
  SystemNode,
  PersonNode,
  ExternalSystemNode,
  ContainerNode,
  ComponentNode,
  ArchFileNode,
  ArchContainerNode,
  LayerClusterNode,
  PortalNode,
} from "./nodes";

export { RelationEdge, c4EdgeTypes } from "./edges/RelationEdge";

export { TONE_STYLES, getTone, ARCH_NODE_SIZES } from "../graph-primitives";
export type { ToneName, ToneStyle } from "../graph-primitives";
export { ArchEdgeRenderer, archEdgeTypes, EDGE_CATEGORY_COLORS, computeEdgeStrokeWidth } from "../graph-primitives";
export type { ArchEdgeData } from "../graph-primitives";

export {
  C4ExportMenu,
  buildC4Svg,
  downloadSvg,
  downloadPng,
  svgToPngBlob,
  exportArchitectureJson,
} from "./export";
export type { C4ExportMenuProps, SvgExportOptions, PngExportOptions, ArchitectureJsonExport } from "./export";

export {
  C4LevelTabs,
  C4Breadcrumb,
  C4Legend,
  C4NodeInspector,
  C4DetailPanel,
  Sidebar,
  ProjectOverview,
  ArchNodeInfo,
  FileExplorer,
  SearchBar,
  FilterPanel,
  NodeTypeCategoryFilters,
  LearnPanel,
  ArchBreadcrumb,
  ArchLegend,
  PersonaSelector,
  ArchTourButton,
  CodeViewer,
  getLanguageFromPath,
  NodeTooltip,
} from "./panels";
export type { C4DetailPanelProps, C4Health, C4DocSummary } from "./panels";
export type { SidebarProps, ArchNodeInfoProps, ArchNodeHealth, NodeTooltipProps } from "./panels";
export type { CodeViewerProps } from "./panels";

export { ExecutionFlowOverlay, DiffOverlay } from "./overlays";
export type { ExecutionFlowEntry } from "./overlays";
export { PathFinderModal } from "./panels";
export { findShortestPath } from "./utils";
export { fuzzyMatch } from "./utils/fuzzy-match";

export type {
  C4Level,
  C4Category,
  C4Person,
  C4System,
  C4ExternalSystem,
  C4Container,
  C4Component,
  C4Relation,
  C4L1,
  C4L2,
  C4L3,
  C4NodeData,
  C4EdgeData,
  ArchNodeType,
  ArchNode,
  ArchEdge,
  ArchLayer,
  ArchTourStep,
  ArchitectureView,
  NavigationLevel,
  Persona,
  DetailLevel,
  SearchMode,
  SearchResult,
  ArchFilters,
  ContainerLayoutResult,
} from "./types";

export { MobileLayout, useIsMobile, MobileBottomNav } from "./mobile";
export type { MobileLayoutProps, MobileTab, MobileBottomNavProps } from "./mobile";

export { THEME, KEYFRAMES } from "./theme/theme-variables";
