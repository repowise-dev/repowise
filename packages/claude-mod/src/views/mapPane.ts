/**
 * The map pane: the Raster and, outside it, the legend (bands with words,
 * Claude's marks, what the trail and the edit reached, and the scope).
 */

import { HEALTH_BAND_LABEL, HEALTH_BAND_ORDER } from "@repowise-dev/types/health";
import { BRAND, DARK_CANVAS, DARK_CANVAS_BAND } from "@repowise-dev/ui/brand";
import { fit } from "../format";
import { MAP_COPY, callersLine, readsLine, scopeParts, type ScopeFacts } from "./copy";
import { box, raster, text, type Node } from "./elements";
import type { MapLayout } from "./map";
import { EDIT_TINT, type ResolvedOverlay } from "./overlay";

export const MAP_KEY = "lens-map";
const MAX_COLUMNS = 512;
const MAX_ROWS = 256;
const MIN_ROWS = 4;
const GAP = 2;
const SEP = " · ";
/** Rows of facts under the swatches: reads, the edit, and the scope over at most two. */
const FACT_ROWS = 4;
/** An inline pane grows with its tree, so it asks for a height that keeps the map squat. */
const INLINE_ROWS_PER_COLUMN = 1 / 6;
const INLINE_MAX_ROWS = 20;

interface Swatch {
  glyph: string;
  color: string;
  label: string;
}

const hexOf = (n: number): string => `#${n.toString(16).padStart(6, "0")}`;

const BAND_SWATCHES: readonly Swatch[] = [
  ...HEALTH_BAND_ORDER.map((band) => ({ glyph: "■", color: DARK_CANVAS_BAND[band], label: HEALTH_BAND_LABEL[band] })),
  { glyph: "■", color: DARK_CANVAS.nodeNeutral, label: MAP_COPY.noScore },
];
const READ: Swatch = { glyph: "□", color: BRAND.accent, label: MAP_COPY.read };
const EDITED: Swatch = { glyph: "◆", color: hexOf(EDIT_TINT), label: MAP_COPY.edited };
const IMPORTER: Swatch = { glyph: "·", color: BRAND.accent, label: MAP_COPY.importer };
const MATCH: Swatch = { glyph: "▪", color: BRAND.accent, label: MAP_COPY.match };

/** Greedy rows: items in order, a new row whenever the next would pass `columns`. */
export function packRows<T>(items: readonly T[], width: (item: T) => number, columns: number, gap: number): T[][] {
  const rows: T[][] = [];
  let used = Infinity;
  for (const item of items) {
    const w = width(item);
    const last = rows[rows.length - 1];
    if (last === undefined || used + gap + w > columns) {
      rows.push([item]);
      used = w;
    } else {
      last.push(item);
      used += gap + w;
    }
  }
  return rows;
}

const swatchWidth = (s: Swatch): number => s.label.length + 2;

/** Legend rows at this width, reserved whatever the marks: the map's height must not move under a blit. */
export function legendRows(columns: number): number {
  const swatchRows = (items: readonly Swatch[]) => packRows(items, swatchWidth, columns, GAP).length;
  return swatchRows(BAND_SWATCHES) + swatchRows([READ, EDITED, IMPORTER, MATCH]) + FACT_ROWS;
}

export interface PaneSize {
  bodyColumns: number;
  placement: "dock" | "inline";
  bodyRows: number;
}

/** The Raster's cell size for a pane: full width, and the height the pane gives (dock) or a squat share (inline). */
export function mapSize(pane: PaneSize): { columns: number; rows: number } {
  const columns = Math.max(1, Math.min(MAX_COLUMNS, pane.bodyColumns));
  const wanted =
    pane.placement === "dock"
      ? pane.bodyRows - legendRows(columns)
      : Math.min(INLINE_MAX_ROWS, Math.round(columns * INLINE_ROWS_PER_COLUMN));
  return { columns, rows: Math.max(MIN_ROWS, Math.min(MAX_ROWS, wanted)) };
}

function swatchRows(items: readonly Swatch[], columns: number): Node[] {
  return packRows(items, swatchWidth, columns, GAP).map((row) =>
    box(
      { flexDirection: "row", columnGap: GAP },
      row.map((s) => box({ flexDirection: "row" }, [text(s.glyph, { color: s.color }), text(` ${s.label}`, { dimColor: true })])),
    ),
  );
}

function marksOf(resolved: ResolvedOverlay): Swatch[] {
  const marks = [READ, EDITED];
  if (resolved.overlay.callers.length > 0) marks.push(IMPORTER);
  if (resolved.overlay.hits.length > 0) marks.push(MATCH);
  return marks;
}

/** Scope parts joined with ` · `, wrapped onto at most two rows so the last parts survive a narrow pane. */
function scopeRows(parts: readonly string[], columns: number): string[] {
  return packRows(parts, (p) => p.length, columns, SEP.length)
    .slice(0, 2)
    .map((row) => row.join(SEP));
}

function factRows(resolved: ResolvedOverlay, scope: ScopeFacts, columns: number): string[] {
  const facts: string[] = [];
  const reads = readsLine(resolved.reads, resolved.matched);
  if (reads !== null) facts.push(reads);
  if (resolved.edit !== null) facts.push(callersLine(resolved.edit.name, resolved.edit.callers, resolved.edit.notDrawn));
  return [...facts, ...scopeRows(scopeParts(scope), columns)];
}

export function legendView(resolved: ResolvedOverlay, scope: ScopeFacts, columns: number): Node[] {
  return [
    ...swatchRows(BAND_SWATCHES, columns),
    ...swatchRows(marksOf(resolved), columns),
    ...factRows(resolved, scope, columns).map((f) => text(fit(f, columns), { dimColor: true, wrap: "truncate-end" })),
  ];
}

export function mapPaneView(layout: MapLayout, cells: string, resolved: ResolvedOverlay, scope: ScopeFacts): Node {
  return box({ key: "lens-map-pane", flexDirection: "column" }, [
    raster({ key: MAP_KEY, columns: layout.columns, rows: layout.rows, cells }),
    ...legendView(resolved, scope, layout.columns),
  ]);
}

/** The pane when there is no map to draw: one line saying why. */
export function noticeView(line: string, columns: number): Node {
  return box({ key: "lens-map-pane", flexDirection: "column" }, [text(fit(line, columns), { dimColor: true, wrap: "truncate-end" })]);
}
