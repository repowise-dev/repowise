/**
 * File-level drawing: the compact file tiles and the section headers of a
 * parent laid out by `layout-files.ts`. Browser code.
 *
 * A tile is a flat rounded box with the file name (middle ellipsis when long)
 * and its health score as a coloured number on the right; no kind or language
 * label, the extension says it, and no hotspot mark, the section header does.
 * A header ("Hotspots · 11") is drawn above the first member of its section,
 * scaled with the tiles, so it moves and culls with them.
 */

import type { Rect } from "./camera";
import { healthColor } from "./card-body";
import { font, textWidth, wrap } from "./card-measure";
import { type FileSection, type FileSectionKind, fileSections, sectionRowH } from "./layout-files";
import type { CardState } from "./nodes";
import type { ZoomPalette } from "./theme";
import type { ZoomNode } from "./types";

const TILE_CORNER_PX = 9;
/** Text px as a share of the tile height, and its bounds (below the min, no text). */
const TILE_TEXT_SHARE = 0.3;
const TILE_TEXT_MIN_PX = 8;
const TILE_TEXT_MAX_PX = 14;
/** Horizontal padding as a share of the tile height. */
const TILE_PAD_SHARE = 0.23;
/** Header baseline above its section's first row, in tile heights. */
const HEADER_LIFT = 0.22;

const SECTION_NAME: Record<FileSectionKind, string> = {
  folders: "Folders",
  hotspots: "Hotspots",
  files: "Other files",
};

interface SectionHeader {
  name: string;
  /** " · N", drawn muted after the name. */
  count: string;
  /** Height of the section's member row, in tile heights. */
  rowH: number;
}

/** A file-level parent's headers, keyed by the first member of each section. */
type TileParent = Map<string, SectionHeader>;

/** Per parent node (node objects are stable per map); null = not file level. */
const tileParents = new WeakMap<ZoomNode, TileParent | null>();

function header(s: FileSection, all: readonly FileSection[]): SectionHeader {
  // Without a Hotspots section the rest are not "other".
  const name = s.kind === "files" && !all.some((o) => o.kind === "hotspots") ? "Files" : SECTION_NAME[s.kind];
  return { name, count: ` · ${s.members.length}`, rowH: sectionRowH(s.kind) };
}

function tileParent(nodes: Map<string, ZoomNode>, parent: ZoomNode): TileParent | null {
  let tp = tileParents.get(parent);
  if (tp !== undefined) return tp;
  // The same children, in the same order, as `computeWorldRects` laid out.
  const kids: ZoomNode[] = [];
  for (const id of parent.children) {
    const c = nodes.get(id);
    if (c) kids.push(c);
  }
  const sections = fileSections(kids);
  tp = sections ? new Map(sections.map((s) => [s.members[0]!.id, header(s, sections)])) : null;
  tileParents.set(parent, tp);
  return tp;
}

const scoreLabels = new Map<number, string>();

/** Interned "7.4" / "10" so a frame builds no strings. */
function scoreLabel(score: number): string {
  const tenths = Math.round(score * 10);
  let s = scoreLabels.get(tenths);
  if (!s) scoreLabels.set(tenths, (s = tenths % 10 === 0 ? String(tenths / 10) : (tenths / 10).toFixed(1)));
  return s;
}

function drawHeader(ctx: CanvasRenderingContext2D, rect: Rect, h: SectionHeader, palette: ZoomPalette, alpha: number): void {
  const tileH = rect.h / h.rowH;
  const px = Math.min(TILE_TEXT_MAX_PX, Math.round(tileH * TILE_TEXT_SHARE));
  if (px < TILE_TEXT_MIN_PX) return;
  const y = Math.round(rect.y - tileH * HEADER_LIFT);
  const x = Math.round(rect.x);
  ctx.textBaseline = "bottom";
  ctx.font = font(600, px);
  ctx.globalAlpha = alpha;
  ctx.fillStyle = palette.nodeText;
  ctx.fillText(h.name, x, y);
  const nameW = textWidth(ctx, 600, px, h.name);
  ctx.font = font(400, px);
  ctx.globalAlpha = alpha * 0.9;
  ctx.fillStyle = palette.textMuted;
  ctx.fillText(h.count, x + nameW, y);
  ctx.textBaseline = "top";
  ctx.globalAlpha = 1;
}

function drawTile(
  ctx: CanvasRenderingContext2D,
  rect: Rect,
  node: ZoomNode,
  palette: ZoomPalette,
  alpha: number,
  state: CardState,
): void {
  ctx.globalAlpha = alpha;
  ctx.beginPath();
  ctx.roundRect(rect.x, rect.y, rect.w, rect.h, Math.max(0, Math.min(TILE_CORNER_PX, rect.h * 0.2, rect.w / 2)));
  ctx.fillStyle = palette.nodeFill;
  ctx.fill();
  ctx.lineWidth = state.selected ? 2 : 1;
  ctx.strokeStyle = state.selected ? palette.accent : state.hovered ? palette.nodeBorderHover : palette.nodeBorder;
  ctx.stroke();

  const px = Math.min(TILE_TEXT_MAX_PX, Math.round(rect.h * TILE_TEXT_SHARE));
  if (px < TILE_TEXT_MIN_PX) {
    ctx.globalAlpha = 1;
    return;
  }
  const pad = Math.round(rect.h * TILE_PAD_SHARE);
  const y = Math.round(rect.y + rect.h / 2);
  const right = rect.x + rect.w - pad;
  ctx.textBaseline = "middle";
  let nameRight = right;
  if (node.health_score !== null) {
    const label = scoreLabel(node.health_score);
    ctx.font = font(600, px);
    ctx.textAlign = "right";
    ctx.fillStyle = healthColor(node.health_score, palette);
    ctx.fillText(label, Math.round(right), y);
    ctx.textAlign = "left";
    nameRight -= textWidth(ctx, 600, px, label) + pad / 2;
  }
  const x = Math.round(rect.x + pad);
  const name = wrap(ctx, 400, px, node.name, nameRight - x, 1, "path")[0];
  if (name) {
    ctx.font = font(400, px);
    ctx.fillStyle = palette.nodeText;
    ctx.fillText(name, x, y);
  }
  ctx.textBaseline = "top";
  ctx.globalAlpha = 1;
}

/**
 * Draw `node` the file-level way when its parent is laid out by
 * `layout-files.ts`: its section header if it opens a section, and the node as
 * a tile if it is a file. Returns false when the caller still draws the normal
 * card (any node outside a file-level parent, and the folders in one).
 */
export function drawFileLevelNode(
  ctx: CanvasRenderingContext2D,
  nodes: Map<string, ZoomNode>,
  rect: Rect,
  node: ZoomNode,
  palette: ZoomPalette,
  alpha: number,
  state: CardState,
): boolean {
  const parent = node.parent_id !== null ? nodes.get(node.parent_id) : undefined;
  const tp = parent ? tileParent(nodes, parent) : null;
  if (!tp) return false;
  const h = tp.get(node.id);
  if (h) drawHeader(ctx, rect, h, palette, alpha);
  if (node.kind !== "file") return false;
  drawTile(ctx, rect, node, palette, alpha, state);
  return true;
}
