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
export type { ZoomCanvasProps, ZoomCanvasHandle } from "./ZoomCanvas";

export { ZoomRenderer } from "./renderer";
export type { ZoomRendererOptions, FrameStats, FlyOptions } from "./renderer";

export { easeInOutCubic, flyDuration, interpolateCamera } from "./camera-anim";
export { focusChain, focusId } from "./focus-path";

export { buildScene, childNodes } from "./scene";
export type { ZoomScene } from "./scene";

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
  clampCamera,
  clampScale,
  fitRoot,
  frameRect,
  panByScreen,
  screenToWorld,
  worldRectToScreen,
  worldToScreen,
  zoomAbout,
} from "./camera";

export { composeRect, computeWorldRects, perimeterPoint, rectContains } from "./geometry";

export { gridDimensions, packLayout } from "./layout";
export type { LayoutChild, PackLayoutOptions } from "./layout";

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
  ZoomRect,
  ZoomMetrics,
  ZoomNode,
  ZoomRelation,
  ZoomMap,
} from "./types";
