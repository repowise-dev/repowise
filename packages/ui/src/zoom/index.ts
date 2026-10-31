/**
 * @repowise-dev/ui/zoom: continuous-zoom knowledge-graph canvas.
 *
 * Public entry point. Host pages mount `<ZoomCanvas data={zoomMap} />` with the
 * map fetched from `/api/graph/{repo_id}/zoom-map`. The renderer, camera and
 * pure tree/fade/cull math are exported too so downstream surfaces (and tests)
 * can reuse them. Everything except `ZoomCanvas` and `renderer`/`theme` is
 * browser-free and unit-testable in isolation.
 */

export { ZoomCanvas } from "./ZoomCanvas";
export type { ZoomCanvasHandle, ZoomCanvasProps } from "./ZoomCanvas";
export { DEFAULT_ZOOM_CANVAS_LABELS } from "./zoom-canvas-labels";
export type { ZoomCanvasLabels } from "./zoom-canvas-labels";
export { KEY_ZOOM_FACTOR } from "./use-zoom-input";

export { ZoomRenderer } from "./renderer";
export type { ZoomRendererOptions, FrameStats, FlyOptions } from "./renderer";

export { easeInOutCubic, flyDuration, interpolateCamera } from "./camera-anim";
export { focusChain, focusId } from "./focus-path";

export { buildScene, childNodes, testLayerIds } from "./scene";
export type { ZoomScene } from "./scene";

export {
  CO_CHANGES,
  describeCap,
  describeRelations,
  formatVerbCounts,
  indexRelationsByNode,
  relationDirections,
  summarizeRelations,
  toggleVerb,
} from "./relation-summary";
export type { RelationDirections, RelationSummary, VerbCount } from "./relation-summary";

export { healthBandLabel, KIND_LABEL, nodeRoles } from "./node-signals";

export {
  CONTAINER_KIND_LABEL,
  containerBadge,
  containerKindLabel,
  containerMembers,
  externalKindLabel,
  externalName,
  externalsReached,
  indexExternals,
} from "./c4";
export type { ExternalIndex, ExternalReach } from "./c4";
export { techName } from "./tech-names";
export { buildSearchIndex, nameWords, searchIndex } from "./search";
export type { SearchEntry, SearchIndex, SearchKind } from "./search";

export { drawScene, pickNode } from "./draw-tree";
export type { DrawOptions, DrawStats, PickEntry } from "./draw-tree";

export { resolveZoomPalette } from "./theme";
export type { ZoomPalette } from "./theme";

export {
  type Camera,
  type Viewport,
  type Rect,
  type ScreenPoint,
  type WorldPoint,
  cameraAfterResize,
  clampCamera,
  clampScale,
  fitRoot,
  fitScale,
  frameRect,
  panByScreen,
  panToReveal,
  viewportCoverage,
  screenToWorld,
  worldRectToScreen,
  worldToScreen,
  zoomAbout,
} from "./camera";

export { composeRect, computeWorldRects, perimeterPoint, rectContains } from "./geometry";

export { gridDimensions, packLayout } from "./layout";
export type { LayoutChild, LayoutRelation, PackLayoutOptions } from "./layout";

export {
  controlPoints,
  facingSide,
  routeEdges,
  slotAnchor,
} from "./edges";
export type {
  EdgeInput,
  EdgeRoute,
  Point,
  RoutedEdge,
  RouteOptions,
  Side,
} from "./edges";

export {
  type FadeThresholds,
  type FadeAlphas,
  expandThresholds,
  fadeAlphas,
  leafCapScale,
  transitionT,
} from "./zoom-transition";

export { isOnScreen, selectChildren } from "./cull";

export type {
  ZoomKind,
  ZoomMetrics,
  ZoomNode,
  ZoomRelation,
  ZoomMap,
  ZoomExternal,
  ZoomExternalKind,
  ZoomContainer,
  ZoomContainerKind,
} from "./types";
