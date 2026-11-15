/**
 * File-level layout: the children of a parent that holds mostly files, laid out
 * as grouped sections of uniform tiles instead of the importance pack. Pure,
 * browser-free and unit-testable.
 *
 * A folder of thirty files packed as staggered cards reads as noise: ragged
 * rows, and every card repeating that it is a file. Here the children split
 * into sections, Folders (normal cards) first, then Hotspots, then the other
 * files, each worst health first. Files are compact tiles of one size in an
 * aligned grid whose column count fits the parent's shape; each section leaves
 * a header band above its first row for `draw-tiles.ts` to label. Like
 * `packLayout`, the result is in parent `[0,1]` space.
 */

import type { Rect } from "./camera";
import { MAX_CHILDREN_DRAWN } from "./constants";
import type { LayoutChild } from "./layout";

/** Share of a parent's children that must be files for the file-level layout. */
export const FILE_LEVEL_SHARE = 0.6;
/** Fewer files than this still pack as cards: a handful of cards reads fine. */
export const FILE_LEVEL_MIN_FILES = 8;

// Section geometry, in tile heights (a tile is 1 high).
/** Tile width : height; a typical file name fits, a long one takes an ellipsis. */
export const TILE_ASPECT = 5.2;
/** Gap between tiles and between folder cards. */
export const TILE_GAP = 0.23;
/** Folder card height: a title row and a footer row. */
export const FOLDER_H = 1.75;
/** Band above a section's first row that holds its header. */
export const HEADER_H = 0.7;
/** Extra space between one section's last row and the next header. */
export const SECTION_GAP = 0.45;
/** Folder cards per row, as a share of the tile columns (they are wider). */
const FOLDER_COL_SHARE = 0.75;

/** The fields the grouping reads; a `ZoomNode` carries them all. */
export interface TileChild extends LayoutChild {
  is_hotspot?: boolean;
  health_score?: number | null;
}

export type FileSectionKind = "folders" | "hotspots" | "files";

export interface FileSection<T extends TileChild = TileChild> {
  kind: FileSectionKind;
  /** Worst health first, unscored last. */
  members: T[];
}

const healthKey = (c: TileChild): number => c.health_score ?? Infinity;

/** Worst health first, unscored last; ties by rank then id, so the order is stable. */
function worstFirst(a: TileChild, b: TileChild): number {
  const ha = healthKey(a);
  const hb = healthKey(b);
  if (ha !== hb) return ha < hb ? -1 : 1;
  return a.sibling_rank - b.sibling_rank || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}

/**
 * The sections a parent's children fall into, or null when they are not
 * mostly files (see `FILE_LEVEL_SHARE`, `FILE_LEVEL_MIN_FILES`) or too many
 * to draw them all (`MAX_CHILDREN_DRAWN`). Empty
 * sections are left out.
 */
export function fileSections<T extends TileChild>(children: readonly T[]): FileSection<T>[] | null {
  let files = 0;
  for (const c of children) if (c.kind === "file") files++;
  if (files < FILE_LEVEL_MIN_FILES || files < children.length * FILE_LEVEL_SHARE) return null;
  // Past the draw cap the scene drops children by rank, which would leave holes
  // in the grid and could drop a section's first member (its header with it).
  if (children.length > MAX_CHILDREN_DRAWN) return null;
  const folders: T[] = [];
  const hotspots: T[] = [];
  const other: T[] = [];
  for (const c of children) (c.kind !== "file" ? folders : c.is_hotspot ? hotspots : other).push(c);
  const out: FileSection<T>[] = [];
  if (folders.length > 0) out.push({ kind: "folders", members: folders.sort(worstFirst) });
  if (hotspots.length > 0) out.push({ kind: "hotspots", members: hotspots.sort(worstFirst) });
  if (other.length > 0) out.push({ kind: "files", members: other.sort(worstFirst) });
  return out;
}

/** Height of one member row of a section, in tile heights. */
export function sectionRowH(kind: FileSectionKind): number {
  return kind === "folders" ? FOLDER_H : 1;
}

/** Columns a section uses when the tiles run `cols` wide. */
function sectionCols(kind: FileSectionKind, cols: number): number {
  return kind === "folders" ? Math.max(1, Math.round(cols * FOLDER_COL_SHARE)) : cols;
}

function blockW(cols: number): number {
  return cols * TILE_ASPECT + (cols - 1) * TILE_GAP;
}

function blockH(sections: readonly FileSection[], cols: number): number {
  let h = Math.max(0, sections.length - 1) * SECTION_GAP;
  for (const s of sections) {
    const rows = Math.ceil(s.members.length / sectionCols(s.kind, cols));
    h += HEADER_H + rows * sectionRowH(s.kind) + (rows - 1) * TILE_GAP;
  }
  return h;
}

/**
 * Lay a mostly-files parent's children out as sections (see `fileSections`),
 * or null when the parent is not one. The tile column count is the one whose
 * block, uniform-fit into the parent, gives the largest tiles; the block is
 * centred. Deterministic.
 */
export function fileLevelLayout(
  children: readonly TileChild[],
  parentAspect: number,
): Map<string, Rect> | null {
  const sections = fileSections(children);
  if (!sections) return null;
  const aspect = parentAspect > 0 ? parentAspect : 1;

  let maxCols = 1;
  for (const s of sections) if (s.kind !== "folders") maxCols = Math.max(maxCols, s.members.length);
  let cols = 1;
  let scale = 0;
  for (let c = 1; c <= maxCols; c++) {
    const s = Math.min(aspect / blockW(c), 1 / blockH(sections, c));
    if (s > scale + 1e-12) {
      scale = s;
      cols = c;
    }
  }

  // Placed in units where x and y share a scale (the parent is `aspect` wide
  // and 1 high), then x is divided by `aspect` into the parent's [0,1] box, so
  // tiles keep their shape once `composeRect` stretches that box back.
  const w = blockW(cols);
  const offX = (aspect - w * scale) / 2;
  const offY = (1 - blockH(sections, cols) * scale) / 2;
  const out = new Map<string, Rect>();
  let y = 0;
  for (const s of sections) {
    y += HEADER_H;
    const n = sectionCols(s.kind, cols);
    const cellW = (w - (n - 1) * TILE_GAP) / n;
    const rowH = sectionRowH(s.kind);
    s.members.forEach((m, i) => {
      const col = i % n;
      const row = Math.floor(i / n);
      out.set(m.id, {
        x: (offX + col * (cellW + TILE_GAP) * scale) / aspect,
        y: offY + (y + row * (rowH + TILE_GAP)) * scale,
        w: (cellW * scale) / aspect,
        h: rowH * scale,
      });
    });
    const rows = Math.ceil(s.members.length / n);
    y += rows * rowH + (rows - 1) * TILE_GAP + SECTION_GAP;
  }
  return out;
}
