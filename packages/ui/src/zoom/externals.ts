/**
 * Where external systems sit on the map. Pure, browser-free and unit-tested.
 *
 * A parent whose children reach external systems (a database, a remote API)
 * keeps a band along its bottom edge: its children pack into the rest
 * (`bandContent`), and the externals sit in the band as dashed C4 cards, each
 * under the child that imports it most, so an external never lands on a card
 * and its connector stays short. The children above are ranked callers over
 * foundations (see `layout.ts`), so the band reads as the layer under the
 * foundations; at the root that is the system-context view.
 *
 * Everything here is resolved once per scene: the band cards, the "+N more"
 * chip and every child -> external connector, routed by `edges.ts`. Geometry
 * is kept in lane units (`LANE_UNITS` across the parent's width) rather than
 * world units, which shrink toward 1e-9 deep in the tree where the router's
 * pixel-sized floors would distort a curve. A frame only scales lane units
 * by the parent's on-screen width.
 */

import type { Rect } from "./camera";
import { externalKindLabel, externalName } from "./c4";
import { type EdgeInput, type EdgeRoute, routeEdges } from "./edges";
import { sameAspect } from "./geometry";
import type { ZoomExternal, ZoomNode, ZoomRelation } from "./types";

/** Share of a banded parent's height given to the band (gaps included). */
export const BAND_FRAC = 0.2;
/** Most externals drawn per parent; the rest fold into one "+N more" chip. */
export const EXTERNALS_MAX_DRAWN = 5;
/** Lane units across a parent's width. */
export const LANE_UNITS = 1000;

const BAND_EDGE = 0.012 * LANE_UNITS; // between the band and the parent's edges
const CARD_ASPECT = 2.1;
const CARD_FILL = 0.6; // share of the band's height a card takes
const CARD_GAP = 0.15; // horizontal gap between cards, in card widths
const MORE_W = 0.6; // the "+N more" chip's size, in card sizes
const MORE_H = 0.5;

export interface ExternalCard {
  /** The external's id (`ext:<name>`), also its pick id. */
  id: string;
  external: ZoomExternal;
  /** In lane units (see `LANE_UNITS`). */
  rect: Rect;
  /** Import edges reaching it from this parent's children. */
  edgeCount: number;
  title: string;
  /** Its kind, e.g. "Database". */
  kind: string;
  /** Import edges in words ("12 imports") and bare ("12") for a narrow card. */
  count: string;
  countShort: string;
}

export interface ExternalLink {
  sourceId: string;
  card: ExternalCard;
  coupling: string;
  edgeCount: number;
  /** In lane units. */
  route: EdgeRoute;
}

export interface ExternalLane {
  /** Drawn externals, most import edges first (placed left to right by user). */
  cards: ExternalCard[];
  /** The "+N more" chip at the band's right end, when some were left out. */
  more: { rect: Rect; label: string } | null;
  /** Connectors per child and per external, strongest first. */
  bySource: Map<string, ExternalLink[]>;
  byTarget: Map<string, ExternalLink[]>;
  /** What it was built from, so a trimmed copy can be rebuilt (see `laneWithout`). */
  inputs: LaneInputs;
}

/** A lane's source: its parent's links to externals and the scene lookups. */
interface LaneInputs {
  rels: readonly ZoomRelation[];
  parentRect: Rect;
  externals: ReadonlyMap<string, ZoomExternal>;
  worldRects: ReadonlyMap<string, Rect>;
}

/**
 * The children of a lane's parent that import `externalId`, most import edges
 * first: what an external's hover card names, and the first is where a
 * double-click on it goes.
 */
export function externalImporters(lane: ExternalLane, externalId: string): readonly ExternalLink[] {
  return lane.byTarget.get(externalId) ?? [];
}

/** The part of a banded parent's rect its children pack into. */
export function bandContent(parent: Rect): Rect {
  return { x: parent.x, y: parent.y, w: parent.w, h: parent.h * (1 - BAND_FRAC) };
}

function push<T>(map: Map<string, T[]>, key: string, v: T): void {
  const list = map.get(key);
  if (list) list.push(v);
  else map.set(key, [v]);
}

/**
 * External relations per parent, keeping only links from a real child of
 * that parent to a known external. Needs no layout, so the scene can decide
 * which parents get a band before it lays them out.
 */
export function bucketExternalRelations(
  relations: readonly ZoomRelation[],
  nodes: ReadonlyMap<string, ZoomNode>,
  externals: ReadonlyMap<string, ZoomExternal>,
): Map<string, ZoomRelation[]> {
  const out = new Map<string, ZoomRelation[]>();
  for (const r of relations) {
    if (!externals.has(r.target_id)) continue;
    if (nodes.get(r.source_id)?.parent_id !== r.parent_id) continue;
    push(out, r.parent_id, r);
  }
  return out;
}

/** A world rect in lane units of `parent`. */
function toLane(r: Rect, parent: Rect): Rect {
  const k = LANE_UNITS / parent.w;
  return { x: (r.x - parent.x) * k, y: (r.y - parent.y) * k, w: r.w * k, h: r.h * k };
}

/** Externals by import edges from this bucket, then repo-wide edges, then id. */
function rankExternals(
  rels: readonly ZoomRelation[],
  externals: ReadonlyMap<string, ZoomExternal>,
): { external: ZoomExternal; edgeCount: number }[] {
  const sums = new Map<string, number>();
  for (const r of rels) sums.set(r.target_id, (sums.get(r.target_id) ?? 0) + r.edge_count);
  const out = [...sums].map(([id, edgeCount]) => ({ external: externals.get(id)!, edgeCount }));
  return out.sort(
    (a, b) =>
      b.edgeCount - a.edgeCount ||
      b.external.edge_count - a.external.edge_count ||
      (a.external.id < b.external.id ? -1 : 1),
  );
}

/**
 * Place `n` cards (and the chip when `more`) in the band, one row, each
 * centred under its `want` x where it can be: sorted by that, pushed right
 * past the card before, then pulled back left from the band's end. Cards
 * shrink when the row would not fit the width. The chip sits at the end.
 */
function bandRects(
  want: readonly number[],
  more: boolean,
  parentH: number,
): { cards: Rect[]; more: Rect | null } {
  const n = want.length;
  const bandH = parentH * BAND_FRAC;
  const span = LANE_UNITS - 2 * BAND_EDGE;
  const units = n + (more ? MORE_W : 0) + CARD_GAP * (n - (more ? 0 : 1));
  const w = Math.min(bandH * CARD_FILL * CARD_ASPECT, span / units);
  const h = w / CARD_ASPECT;
  const gap = w * CARD_GAP;
  const items = want.map((x, i) => ({ i, w, x: x - w / 2 }));
  items.sort((p, q) => p.x - q.x || p.i - q.i);
  if (more) items.push({ i: n, w: w * MORE_W, x: LANE_UNITS });
  let end = BAND_EDGE - gap;
  for (const it of items) {
    it.x = Math.max(it.x, end + gap);
    end = it.x + it.w;
  }
  let start = LANE_UNITS - BAND_EDGE + gap;
  for (let k = items.length - 1; k >= 0; k--) {
    const it = items[k]!;
    it.x = Math.min(it.x, start - gap - it.w);
    start = it.x;
  }
  const top = parentH - bandH + (bandH - h) / 2;
  const cards: Rect[] = new Array<Rect>(n);
  let chip: Rect | null = null;
  for (const it of items) {
    if (it.i < n) cards[it.i] = { x: it.x, y: top, w, h };
    else chip = { x: it.x, y: top + (h - h * MORE_H) / 2, w: it.w, h: h * MORE_H };
  }
  return { cards, more: chip };
}

/**
 * A lane in three stages: rank the parent's externals, place the strongest
 * in the band, then route each child's links to its card. `laneWithout`
 * runs the same stages over fewer links, so a trimmed lane re-places and
 * re-routes exactly as a fresh one would.
 */
function buildLane(source: LaneInputs): ExternalLane {
  const ranked = rankExternals(source.rels, source.externals);
  const { cards, more } = placeCards(ranked, source);
  return { cards, more, ...routeLinks(source, cards), inputs: source };
}

/** The centre x (lane units) of the child importing each external most. */
function topUserX({ rels, parentRect, worldRects }: LaneInputs): Map<string, number> {
  const best = new Map<string, ZoomRelation>();
  for (const r of rels) {
    const cur = best.get(r.target_id);
    if (!cur || r.edge_count > cur.edge_count) best.set(r.target_id, r);
  }
  const out = new Map<string, number>();
  for (const [id, r] of best) {
    const lr = toLane(worldRects.get(r.source_id)!, parentRect);
    out.set(id, lr.x + lr.w / 2);
  }
  return out;
}

/** The strongest externals as cards in the band, under their top users; the rest as a chip. */
function placeCards(
  ranked: readonly { external: ZoomExternal; edgeCount: number }[],
  source: LaneInputs,
): Pick<ExternalLane, "cards" | "more"> {
  const { parentRect } = source;
  const shown = ranked.slice(0, EXTERNALS_MAX_DRAWN);
  const hidden = ranked.length - shown.length;
  const userX = topUserX(source);
  const band = bandRects(
    shown.map(({ external }) => userX.get(external.id)!),
    hidden > 0,
    (parentRect.h / parentRect.w) * LANE_UNITS,
  );
  const cards = shown.map(({ external, edgeCount }, i): ExternalCard => ({
    id: external.id,
    external,
    rect: band.cards[i]!,
    edgeCount,
    title: externalName(external),
    kind: externalKindLabel(external.kind),
    count: `${edgeCount} ${edgeCount === 1 ? "import" : "imports"}`,
    countShort: String(edgeCount),
  }));
  return { cards, more: band.more ? { rect: band.more, label: `+${hidden} more` } : null };
}

/** Route each child's link to its drawn card, indexed both ways, strongest first. */
function routeLinks(
  { rels, parentRect, worldRects }: LaneInputs,
  cards: readonly ExternalCard[],
): Pick<ExternalLane, "bySource" | "byTarget"> {
  const byId = new Map(cards.map((c) => [c.id, c] as const));
  // Route to each card's top edge as a sliver: the router picks the side
  // facing the source, and a source far to one side would otherwise take a
  // link on the card's left or right, through its neighbour in the band.
  const rects = new Map<string, Rect>(
    cards.map((c) => [c.id, { x: c.rect.x, y: c.rect.y, w: c.rect.w, h: 1e-3 }] as const),
  );
  const inputs: EdgeInput[] = [];
  for (const r of rels) {
    if (!byId.has(r.target_id)) continue;
    if (!rects.has(r.source_id)) rects.set(r.source_id, toLane(worldRects.get(r.source_id)!, parentRect));
    inputs.push({
      id: `${r.source_id} ${r.target_id}`,
      sourceId: r.source_id,
      targetId: r.target_id,
      coupling: r.coupling,
      edgeCount: r.edge_count,
    });
  }

  const bySource = new Map<string, ExternalLink[]>();
  const byTarget = new Map<string, ExternalLink[]>();
  for (const e of routeEdges(inputs, rects)) {
    const link: ExternalLink = {
      sourceId: e.sourceId,
      card: byId.get(e.targetId)!,
      coupling: e.coupling,
      edgeCount: e.edgeCount,
      route: e.route,
    };
    push(bySource, e.sourceId, link);
    push(byTarget, e.targetId, link);
  }
  const strongest = (a: ExternalLink, b: ExternalLink): number => b.edgeCount - a.edgeCount;
  for (const list of bySource.values()) list.sort(strongest);
  for (const list of byTarget.values()) list.sort(strongest);
  return { bySource, byTarget };
}

/**
 * Each parent's last lane and the parent aspect it was built for. A lane is
 * laid out in the parent's own units, so it holds for any layout that keeps
 * the parent's shape (and its pack, see `PackCache`).
 */
export type LaneCache = Map<string, { aspect: number; lane: ExternalLane }>;

/**
 * One lane per laid-out parent with external links from laid-out children.
 * Built once per scene; a frame reads it and allocates nothing. `cache`, when
 * given, is read and refreshed; it is only valid for the same map.
 */
export function buildExternalLanes(
  buckets: ReadonlyMap<string, ZoomRelation[]>,
  externals: ReadonlyMap<string, ZoomExternal>,
  worldRects: ReadonlyMap<string, Rect>,
  cache?: LaneCache,
): Map<string, ExternalLane> {
  const out = new Map<string, ExternalLane>();
  for (const [parentId, rels] of buckets) {
    const parentRect = worldRects.get(parentId);
    if (!parentRect || parentRect.w <= 0) continue;
    const aspect = parentRect.w / parentRect.h;
    const hit = cache?.get(parentId);
    if (hit && sameAspect(hit.aspect, aspect)) {
      out.set(parentId, hit.lane);
      continue;
    }
    const live = rels.filter((r) => worldRects.has(r.source_id));
    if (live.length === 0) continue;
    const lane = buildLane({ rels: live, parentRect, externals, worldRects });
    cache?.set(parentId, { aspect, lane });
    out.set(parentId, lane);
  }
  return out;
}

const trims = new WeakMap<ExternalLane, Map<string, ExternalLane>>();

/**
 * `lane` without the externals in `shown`, re-placed: drawn in an outer band
 * while a band inside it already draws them, so a system shows once. Memoized
 * per lane and set, so it is built once per focus change, never per frame.
 */
export function laneWithout(lane: ExternalLane, shown: ReadonlySet<string>): ExternalLane {
  if (!lane.cards.some((c) => shown.has(c.id))) return lane;
  const key = [...shown].sort().join("\u0000");
  let byKey = trims.get(lane);
  if (!byKey) trims.set(lane, (byKey = new Map()));
  let out = byKey.get(key);
  if (!out) {
    const rels = lane.inputs.rels.filter((r) => !shown.has(r.target_id));
    byKey.set(key, (out = buildLane({ ...lane.inputs, rels })));
  }
  return out;
}

/**
 * The lanes to draw along a focus chain (root first): walking up from the
 * deepest, each band drops the externals a band inside it already draws, so an
 * external shows once, in the innermost band on the way to the focus. Only the
 * lanes that change are returned; every other parent draws its own.
 */
export function focusLanes(
  lanes: ReadonlyMap<string, ExternalLane>,
  chain: readonly { id: string }[],
): Map<string, ExternalLane> {
  const out = new Map<string, ExternalLane>();
  const shown = new Set<string>();
  for (let i = chain.length - 1; i >= 0; i--) {
    const lane = lanes.get(chain[i]!.id);
    if (!lane) continue;
    const drawn = shown.size > 0 ? laneWithout(lane, shown) : lane;
    if (drawn !== lane) out.set(chain[i]!.id, drawn);
    for (const c of drawn.cards) shown.add(c.id);
  }
  return out;
}
