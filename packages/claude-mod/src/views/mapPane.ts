/**
 * The map pane: the Raster, then outside it the story strip (what the turn
 * searched, opened, edited and reaches; it is the map's legend), the health
 * legend when health colours are on, the scope, and the health toggle.
 * The rows under the map are reserved whatever they hold, so the map's height
 * never moves under a blit.
 */

import { HEALTH_BAND_LABEL, HEALTH_BAND_ORDER } from "@repowise-dev/types/health";
import { DARK_CANVAS, DARK_CANVAS_BAND } from "@repowise-dev/ui/brand";
import { fit } from "../format";
import type { Reach, Story } from "../model/story";
import {
  MAP_COPY,
  REACHES_FAILED,
  SCOPE_REST,
  STORY_COPY,
  indexedPart,
  linesPart,
  namedByPart,
  reachesPart,
  reachesPending,
  scopeParts,
  searchedPart,
  type ScopeFacts,
} from "./copy";
import { box, button, raster, text, type Node } from "./elements";
import { MAP_PALETTES, type MapLayout, type MapStyle } from "./map";

export const MAP_KEY = "lens-map";
export const HEALTH_KEY = "lens-map-health";
const MAX_COLUMNS = 512;
const MAX_ROWS = 256;
const MIN_ROWS = 4;
const GAP = 2;
const SEP = " · ";
/** The story strip's four rows, the scope's two, and the toggle's one. */
const STORY_ROWS = 4;
const SCOPE_ROWS = 2;
const TOGGLE_ROWS = 1;
const HEAD = 10;
/** Importers named on the REACHES row before `+N`. */
const REACH_NAMES = 3;
/** An inline pane grows with its tree, so it asks for a height that keeps the map squat. */
const INLINE_ROWS_PER_COLUMN = 1 / 6;
const INLINE_MAX_ROWS = 20;

export interface Swatch {
  glyph: string;
  color: string;
  label: string;
}

const hexOf = (n: number): string => `#${n.toString(16).padStart(6, "0")}`;

const BAND_SWATCHES: readonly Swatch[] = [
  ...HEALTH_BAND_ORDER.map((band) => ({ glyph: "■", color: DARK_CANVAS_BAND[band], label: HEALTH_BAND_LABEL[band] })),
  { glyph: "■", color: DARK_CANVAS.nodeNeutral, label: MAP_COPY.noScore },
];

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

export const swatchWidth = (s: Swatch): number => s.label.length + 2;

/** Rows under the map at this width, reserved whatever they hold: the map's height must not move under a blit. */
export function legendRows(columns: number, health = false): number {
  const bands = health ? packRows(BAND_SWATCHES, swatchWidth, columns, GAP).length : 0;
  return STORY_ROWS + bands + SCOPE_ROWS + TOGGLE_ROWS;
}

export interface PaneSize {
  bodyColumns: number;
  placement: "dock" | "inline";
  bodyRows: number;
}

/** The Raster's cell size for a pane: full width, and the height the pane gives (dock) or a squat share (inline). */
export function mapSize(pane: PaneSize, health = false): { columns: number; rows: number } {
  const columns = Math.max(1, Math.min(MAX_COLUMNS, pane.bodyColumns));
  const wanted =
    pane.placement === "dock"
      ? pane.bodyRows - legendRows(columns, health)
      : Math.min(INLINE_MAX_ROWS, Math.round(columns * INLINE_ROWS_PER_COLUMN));
  return { columns, rows: Math.max(MIN_ROWS, Math.min(MAX_ROWS, wanted)) };
}

/** Legend swatches, a glyph in its color and a dim word, packed into rows of `columns`. */
export function swatchRows(items: readonly Swatch[], columns: number): Node[] {
  return packRows(items, swatchWidth, columns, GAP).map((row) =>
    box(
      { flexDirection: "row", columnGap: GAP },
      row.map((s) => box({ flexDirection: "row" }, [text(s.glyph, { color: s.color }), text(` ${s.label}`, { dimColor: true })])),
    ),
  );
}

/**
 * Scope parts joined with ` · `, wrapped onto at most two rows. When they
 * need more, the parts that do not change the reading leave first (the "rest"
 * note, then the index age), so the counts and the cap clause survive.
 */
function scopeRows(scope: ScopeFacts, columns: number): string[] {
  const rows = (parts: readonly string[]) => packRows(parts, (p) => p.length, columns, SEP.length);
  let parts = scopeParts(scope);
  for (const optional of [SCOPE_REST, scope.indexed === null ? null : indexedPart(scope.indexed)]) {
    if (rows(parts).length <= 2) break;
    parts = parts.filter((p) => p !== optional);
  }
  return rows(parts)
    .slice(0, 2)
    .map((row) => row.join(SEP));
}

/** One piece of a story row, in a color or the terminal's own. */
interface Seg {
  text: string;
  color?: string;
}

/** A story row: the dim head, then its pieces, cut to fit `columns`. */
function storyRow(head: string, segs: readonly Seg[], columns: number): Node {
  let room = Math.max(0, columns - HEAD);
  const parts: Node[] = [text(head.padEnd(HEAD), { dimColor: true })];
  for (const s of segs) {
    if (room <= 0) break;
    const shown = fit(s.text, room);
    room -= shown.length;
    parts.push(text(shown, s.color === undefined ? {} : { color: s.color }));
  }
  return box({ flexDirection: "row" }, parts);
}

function reachSegs(reach: Reach, plum: string): Seg[] {
  if (reach.callers.status === "loading") return [{ text: reachesPending(reach.edit.slice(reach.edit.lastIndexOf("/") + 1)) }];
  if (reach.callers.status === "failed") return [{ text: REACHES_FAILED }];
  const paths = [...reach.callers.paths].sort((a, b) => Number(reach.opened.has(b.toLowerCase())) - Number(reach.opened.has(a.toLowerCase())));
  const names = paths.slice(0, REACH_NAMES).map((p) => ({ name: p.slice(p.lastIndexOf("/") + 1), opened: reach.opened.has(p.toLowerCase()) }));
  return [{ text: reachesPart(paths.length, names), color: plum }];
}

function searchedSegs(story: Story): Seg[] {
  return story.searched.length === 0 ? [] : [{ text: story.searched.map((s) => searchedPart(s.pattern, s.hits)).join(SEP) }];
}

/** The opened files, then the first one a Repowise reply named, in plum. */
function openedSegs(story: Story, plum: string): Seg[] {
  if (story.opened.length === 0) return [];
  const named = story.opened.find((o) => o.namedBy !== null);
  const names = { text: story.opened.map((o) => o.name).join(SEP) };
  return named === undefined ? [names] : [names, { text: `  ${namedByPart(named.name, named.namedBy as string)}`, color: plum }];
}

function editedSegs(story: Story, amber: string): Seg[] {
  return story.edited.map((e, i) => ({ text: `${i === 0 ? "" : SEP}◉ ${e.name}${linesPart(e.lines)}`, color: amber }));
}

/** The story strip: a row for each of searched, opened, edited and reaches, only once it has something to say. */
export function storyView(story: Story, reach: Reach | null, style: MapStyle, columns: number): Node[] {
  const pal = MAP_PALETTES[style.theme];
  const rows: [string, Seg[]][] = [
    [STORY_COPY.searched, searchedSegs(story)],
    [STORY_COPY.opened, openedSegs(story, hexOf(pal.importer))],
    [STORY_COPY.edited, editedSegs(story, hexOf(pal.edit))],
    [STORY_COPY.reaches, reach === null ? [] : reachSegs(reach, hexOf(pal.importer))],
  ];
  return rows.filter(([, segs]) => segs.length > 0).map(([head, segs]) => storyRow(head, segs, columns));
}

export interface MapPaneParts {
  story: Story;
  reach: Reach | null;
  scope: ScopeFacts;
}

/** Everything under the drawing: the story (or a quiet line), the health legend when on, the scope, and the toggle. */
export function legendView(parts: MapPaneParts, style: MapStyle, columns: number): Node[] {
  const story = storyView(parts.story, parts.reach, style, columns);
  const quiet = story.length === 0 ? [text(fit(MAP_COPY.quiet, columns), { dimColor: true, wrap: "truncate-end" })] : story;
  return [
    ...quiet,
    ...(style.health ? swatchRows(BAND_SWATCHES, columns) : []),
    ...scopeRows(parts.scope, columns).map((f) => text(fit(f, columns), { dimColor: true, wrap: "truncate-end" })),
    button(HEALTH_KEY, "h", style.health ? MAP_COPY.healthOn : MAP_COPY.healthOff, true),
  ];
}

export function mapPaneView(layout: MapLayout, cells: string, parts: MapPaneParts): Node {
  return box({ key: "lens-map-pane", flexDirection: "column" }, [
    raster({ key: MAP_KEY, columns: layout.columns, rows: layout.rows, cells }),
    ...legendView(parts, layout.style, layout.columns),
  ]);
}

/** The pane when there is no map to draw: one line saying why. */
export function noticeView(line: string, columns: number): Node {
  return box({ key: "lens-map-pane", flexDirection: "column" }, [text(fit(line, columns), { dimColor: true, wrap: "truncate-end" })]);
}
