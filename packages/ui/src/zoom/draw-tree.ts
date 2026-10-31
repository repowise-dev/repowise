/**
 * Recursive clip-and-scale draw of the containment tree. Browser code.
 *
 * Each node resolves to a screen rect from its absolute world rect and the
 * camera. We draw the node's card (fading out as `t` grows), then clip to its
 * rect and recurse into the top-N children (fading in), so a card *visibly
 * opens into its parts* as you zoom. Off-screen subtrees, sub-pixel nodes and
 * fully-faded subtrees are skipped, and a density cap bounds how many children
 * are drawn at any depth, keeping the live node count bounded on huge trees.
 */

import { type Camera, type Rect, type Viewport, worldRectToScreen } from "./camera";
import { ALPHA_EPSILON, MIN_DRAW_PX } from "./constants";
import { cardPreview } from "./card-preview";
import { isOnScreen } from "./cull";
import { drawEdges } from "./draw-edges";
import { drawFileLevelNode } from "./draw-tiles";
import { drawGrid } from "./draw-grid";
import { drawEdgeLabels } from "./edge-labels";
import type { RoutedEdge } from "./edges";
import { drawExternalCards, drawExternalLinks, externalCardRects } from "./external-draw";
import type { ExternalLane } from "./externals";
import { type CardState, drawCard } from "./nodes";
import type { PaperTexture } from "./paper";
import { type DrawStats, createDrawStats, pushPick, resetDrawStats } from "./pick";
import { childNodes, type ZoomScene } from "./scene";
import type { ZoomPalette } from "./theme";
import type { ZoomNode } from "./types";
import {
  type FadeThresholds,
  expandThresholds,
  fadeAlphas,
  leafCapScale,
  transitionT,
} from "./zoom-transition";

export { pickNode } from "./pick";
export type { DrawStats, PickEntry } from "./pick";

export interface DrawOptions {
  selectedId: string | null;
  hoveredId: string | null;
  lowDetail: boolean;
  /**
   * The camera is zooming or flying. Cards drop their drop shadow (each one is a
   * blur pass, about half the raster cost of a frame) and keep everything else;
   * the renderer repaints with shadows once the camera settles.
   */
  motion?: boolean;
  /** Shared ruled-paper card texture; null until the asset loads (or on SSR). */
  paper?: PaperTexture | null;
  /**
   * Draw only relations whose verb (`ZoomRelation.label`) is in this set, or
   * every relation when null. A set because a box's relations commonly mix
   * verbs (calls, imports and the rest of the vocabulary), so a reader picks
   * several. Null rather than "the set of all verbs" so the unfiltered case
   * costs no lookup and needs no knowledge of the vocabulary.
   */
  relationVerbs?: ReadonlySet<string> | null;
  /**
   * Cards outside this set draw dimmed (a container picked in the map key), or
   * none when null. Each card is tested alone, so a dimmed subtree never
   * compounds its alpha down the levels.
   */
  highlightIds?: ReadonlySet<string> | null;
  /**
   * Rails drawn in place of a parent's own, keyed by parent id: along the
   * focus chain an outer rail drops what an inner one shows (see `focusLanes`).
   */
  lanes?: ReadonlyMap<string, ExternalLane> | null;
}

/** Alpha of a card outside `DrawOptions.highlightIds`. */
const HIGHLIGHT_DIM_ALPHA = 0.28;

const NO_ARROWS: readonly RoutedEdge[] = [];

const newRect = (): Rect => ({ x: 0, y: 0, w: 0, h: 0 });

/** Shrink a rect about its centre by `scale` into `out` (used for the leaf cap). */
function shrinkAboutCentre(rect: Rect, scale: number, out: Rect): Rect {
  const w = rect.w * scale;
  const h = rect.h * scale;
  out.x = rect.x + (rect.w - w) / 2;
  out.y = rect.y + (rect.h - h) / 2;
  out.w = w;
  out.h = h;
  return out;
}

function rectAt(list: Rect[], depth: number): Rect {
  return list[depth] ?? (list[depth] = newRect());
}

/**
 * One frame's walk of the tree. A single instance serves every frame (a draw
 * is synchronous and never re-enters), so the walk allocates no closures, card
 * states or screen rects. Screen rects are kept one per depth because a card's
 * rect is still read after its children have drawn.
 */
class DrawPass {
  private ctx!: CanvasRenderingContext2D;
  private scene!: ZoomScene;
  private cam!: Camera;
  private vp!: Viewport;
  private palette!: ZoomPalette;
  private opts!: DrawOptions;
  private stats!: DrawStats;
  private thresholds!: FadeThresholds;
  private paper: CanvasPattern | null = null;
  /** The node under the cursor, else the selected one: whose relations lift. */
  private focusId: string | null = null;
  private edgesOn = false;
  private motion = false;
  private highlight: ReadonlySet<string> | null = null;

  private readonly screens: Rect[] = [];
  private readonly capped: Rect[] = [];
  private readonly card: CardState = { selected: false, hovered: false, lowDetail: false, motion: false, preview: null };
  private readonly labelArea = newRect();
  private readonly labelKey: unknown[] = [];
  /** The parent whose arrow labels `labelCards` resolves the card rects for. */
  private labelKids: readonly ZoomNode[] = [];
  private labelLane: ExternalLane | undefined;
  private labelScreen: Rect = newRect();

  begin(
    ctx: CanvasRenderingContext2D,
    scene: ZoomScene,
    cam: Camera,
    vp: Viewport,
    palette: ZoomPalette,
    opts: DrawOptions,
    stats: DrawStats,
  ): void {
    this.ctx = ctx;
    this.scene = scene;
    this.cam = cam;
    this.vp = vp;
    this.palette = palette;
    this.opts = opts;
    this.stats = stats;
    this.thresholds = expandThresholds(vp.w);
    // Resolve the paper pattern once per frame (lazily built against this ctx).
    this.paper = opts.paper?.get(ctx) ?? null;
    // Hover wins over the selection for immediate feedback.
    this.focusId = opts.hoveredId ?? opts.selectedId;
    this.edgesOn = !opts.lowDetail && this.focusId !== null;
    this.motion = opts.motion ?? false;
    this.highlight = opts.highlightIds ?? null;
  }

  /** Drop the frame's references so a released scene or context can be collected. */
  end(): void {
    this.ctx = undefined as unknown as CanvasRenderingContext2D;
    this.scene = undefined as unknown as ZoomScene;
    this.opts = undefined as unknown as DrawOptions;
    this.stats = undefined as unknown as DrawStats;
    this.paper = null;
    this.highlight = null;
    this.card.preview = null;
    this.labelKids = NO_KIDS;
    this.labelLane = undefined;
    this.labelKey.length = 0;
  }

  /** The node's screen rect, or null (counted as culled) when it is off screen or too small. */
  private visibleRect(node: ZoomNode, depth: number): Rect | null {
    const world = this.scene.worldRects.get(node.id);
    if (!world) return null;
    const screen = worldRectToScreen(this.cam, this.vp, world, rectAt(this.screens, depth));
    if (!isOnScreen(screen, this.vp) || screen.w < MIN_DRAW_PX || screen.h < MIN_DRAW_PX) {
      this.stats.culled++;
      return null;
    }
    return screen;
  }

  node(node: ZoomNode, inheritedAlpha: number, depth: number): void {
    const screen = this.visibleRect(node, depth);
    if (!screen) return;
    const kids = childNodes(this.scene, node);
    const hasChildren = kids.length > 0;
    const t = transitionT(screen.w, this.thresholds, hasChildren);
    const { body, child } = fadeAlphas(inheritedAlpha, t);
    this.body(node, screen, depth, t, body, hasChildren);
    if (hasChildren && child > ALPHA_EPSILON) this.children(node, kids, screen, child, depth);
  }

  /** The card itself (a leaf capped once it fills enough of the screen) and its hit-test entry. */
  private body(node: ZoomNode, screen: Rect, depth: number, t: number, alpha: number, hasChildren: boolean): void {
    const cap = leafCapScale(screen.w, this.thresholds, hasChildren);
    const drawn = cap < 1 ? shrinkAboutCentre(screen, cap, rectAt(this.capped, depth)) : screen;
    if (alpha > ALPHA_EPSILON) {
      const dim = this.highlight !== null && !this.highlight.has(node.id) ? HIGHLIGHT_DIM_ALPHA : 1;
      const card = this.card;
      card.selected = node.id === this.opts.selectedId;
      card.hovered = node.id === this.opts.hoveredId;
      card.lowDetail = this.opts.lowDetail;
      card.motion = this.motion;
      card.preview = hasChildren ? cardPreview(this.scene.nodes, node) : null;
      // A file in a mostly-files parent draws as a tile (see `draw-tiles.ts`).
      if (!drawFileLevelNode(this.ctx, this.scene.nodes, drawn, node, this.palette, alpha * dim, card)) {
        drawCard(this.ctx, drawn, node, this.palette, alpha * dim, card, t, this.paper);
      }
    }
    const stats = this.stats;
    stats.drawn++;
    if (depth > stats.maxDepthDrawn) stats.maxDepthDrawn = depth;
    pushPick(stats, node.id, drawn, depth);
  }

  /**
   * Clip to the card and draw what it opens into: its arrows, its external
   * rail and links, every child (density-capped by the scene; each self-culls),
   * then the arrow labels over all of it.
   */
  private children(node: ZoomNode, kids: readonly ZoomNode[], screen: Rect, alpha: number, depth: number): void {
    const { ctx, focusId, palette } = this;
    ctx.save();
    ctx.beginPath();
    ctx.rect(screen.x, screen.y, screen.w, screen.h);
    ctx.clip();

    const arrows = this.arrows(node, kids, alpha);
    const lane = this.opts.lanes?.get(node.id) ?? this.scene.externalLanes.get(node.id);
    if (lane && this.edgesOn) drawExternalLinks(ctx, lane, screen, palette, alpha, focusId);
    for (const kid of kids) this.node(kid, alpha, depth + 1);
    if (lane) drawExternalCards(ctx, lane, screen, palette, alpha, focusId, this.stats, depth + 1);
    if (arrows.length > 0 && !this.motion) this.labels(node, kids, lane, screen, arrows, alpha);

    ctx.restore();
  }

  private arrows(node: ZoomNode, kids: readonly ZoomNode[], alpha: number): readonly RoutedEdge[] {
    const focusId = this.focusId;
    if (!this.edgesOn || focusId === null || !this.scene.relationsByParent.has(node.id)) return NO_ARROWS;
    const { ctx, scene, cam, vp, palette } = this;
    return drawEdges(ctx, scene, node, kids, cam, vp, palette, alpha, focusId, this.opts.relationVerbs ?? null);
  }

  /**
   * Labels go over the cards so a caption is never hidden under one, and only
   * on a settled frame: their layout is reused until the camera, focus or
   * filter changes (see `edge-labels.ts`). They draw inside this card's clip,
   * so they are laid out inside it too.
   */
  private labels(
    node: ZoomNode,
    kids: readonly ZoomNode[],
    lane: ExternalLane | undefined,
    screen: Rect,
    arrows: readonly RoutedEdge[],
    alpha: number,
  ): void {
    const key = this.labelKey;
    key.length = 0;
    key.push(this.scene, this.cam, this.vp, this.focusId, node.id, this.opts.relationVerbs ?? null);
    this.labelKids = kids;
    this.labelLane = lane;
    this.labelScreen = screen;
    const area = this.labelArea;
    area.x = Math.max(0, screen.x);
    area.y = Math.max(0, screen.y);
    area.w = Math.min(this.vp.w, screen.x + screen.w) - area.x;
    area.h = Math.min(this.vp.h, screen.y + screen.h) - area.y;
    drawEdgeLabels(this.ctx, key, arrows, area, this.palette, alpha, this.labelCards);
  }

  /** Screen rects of the cards a label should avoid; runs only when the layout does. */
  private readonly labelCards = (): Rect[] => {
    const out: Rect[] = [];
    for (const k of this.labelKids) {
      const wr = this.scene.worldRects.get(k.id);
      if (wr) out.push(worldRectToScreen(this.cam, this.vp, wr));
    }
    return this.labelLane ? out.concat(externalCardRects(this.labelLane, this.labelScreen)) : out;
  };
}

const NO_KIDS: readonly ZoomNode[] = [];
const pass = new DrawPass();

/**
 * Paint one frame and record it for hit-testing in `out`. A renderer passes
 * the same stats every frame so a repaint allocates none; read a frame's
 * stats before the next draw into them.
 */
export function drawScene(
  ctx: CanvasRenderingContext2D,
  scene: ZoomScene,
  cam: Camera,
  vp: Viewport,
  palette: ZoomPalette,
  opts: DrawOptions,
  out: DrawStats = createDrawStats(),
): DrawStats {
  resetDrawStats(out);
  ctx.fillStyle = palette.bg;
  ctx.fillRect(0, 0, vp.w, vp.h);
  drawGrid(ctx, cam, vp, palette);

  const root = scene.nodes.get(scene.rootId);
  if (!root) return out;
  pass.begin(ctx, scene, cam, vp, palette, opts, out);
  try {
    pass.node(root, 1, 0);
  } finally {
    pass.end();
  }
  return out;
}
