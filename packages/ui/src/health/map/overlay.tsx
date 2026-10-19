"use client";

/**
 * The hover card, and the bound on how many search matches take a ring.
 *
 * The rings themselves paint on the map's top canvas, above the field, so
 * hover and typing repaint a few marks and never the thousands of nodes.
 */

import { performanceSentence, scoreText } from "./lens";
import type { MapArea } from "./areas";
import type { AreaAnalysed } from "./fills";
import type { MapCanvasLabels } from "./canvas-labels";
import type { CodeHealthMapFile, CodeHealthOverlay } from "./types";

/** Match rings drawn at once. Beyond this the field is the answer, not a mark. */
export const SEARCH_MARK_CAP = 250;

/**
 * Card box, for the edge flip.
 *
 * Approximate on purpose: the card sizes to its content, and this only has to
 * decide which side of the pointer it opens on.
 */
const CARD_W = 300;
const CARD_H = 84;
/** Gap between the pointer and the card, so the cursor never sits on it. */
const CARD_GAP = 16;

/**
 * Pointer-bound decoding, in the active lens's terms, at the pointer.
 *
 * Hover answers "what is this and what does the current lens say about it".
 * The lens's own sentence leads, so the card and the inspector describe the
 * same object the same way.
 *
 * It follows the cursor rather than sitting in a corner. On a field of
 * thousands of nodes a fixed card makes every identification a round trip
 * across the canvas and back, and by the time the eye returns the pointer has
 * usually left the node it was asking about. The card flips to the other side
 * of the pointer near an edge so it is never clipped by the container.
 */
/** What a folder's colour was read from, in its own counts. */
function analysedLine(a: AreaAnalysed | null, labels: MapCanvasLabels): string {
  if (!a) return labels.notLoaded;
  const read = labels.analysedFiles(a.analysed, a.loaded);
  if (a.flagged === undefined || a.analysed === 0) return read;
  return `${labels.flaggedFiles(a.flagged, a.analysed)} · ${read}`;
}

/** Where a card opens beside the pointer, flipped near an edge. */
function cardPosition(x: number, y: number, width: number, height: number) {
  const left = x + CARD_GAP + CARD_W > width ? x - CARD_GAP - CARD_W : x + CARD_GAP;
  const top = y + CARD_GAP + CARD_H > height ? y - CARD_GAP - CARD_H : y + CARD_GAP;
  return { left: Math.max(0, left), top: Math.max(0, top) };
}

const CARD =
  "pointer-events-none absolute z-20 w-max max-w-[300px] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1.5 shadow-md";

/**
 * An area under the pointer: its name, the lens's figure, and how big it is.
 * A slot is the files sitting directly in the area, beside its sub-areas.
 */
export function AreaHoverCard({
  area,
  name,
  slot,
  figure,
  lensLabel,
  analysed,
  lensCoversFiles,
  labels,
  x,
  y,
  width,
  height,
}: {
  labels: MapCanvasLabels;
  area: MapArea;
  name: string;
  slot: boolean;
  figure: string | null;
  lensLabel: string;
  /** How many of its loaded files the lens analysed, or null when none are loaded. */
  analysed: AreaAnalysed | null;
  /** The lens tells analysed files apart, so the card says how many it read. */
  lensCoversFiles: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
}) {
  const files = slot ? area.directFileCount : area.fileCount;
  return (
    <div data-testid="map-area-card" className={CARD} style={cardPosition(x, y, width, height)}>
      <div className="break-all font-mono text-xs font-medium leading-snug text-[var(--color-text-primary)]">
        {slot ? labels.directFiles(name || area.path) : name}
      </div>
      <div className="mt-0.5 text-[10px] leading-tight text-[var(--color-text-tertiary)] tabular-nums">
        {labels.fileCount(files)}
        {!slot && figure ? ` · ${lensLabel.toLowerCase()} ${figure}` : ""}
        {!slot ? ` · ${labels.openHint}` : ""}
      </div>
      {lensCoversFiles && !slot ? (
        <div className="mt-0.5 text-[10px] leading-tight text-[var(--color-text-tertiary)] tabular-nums">
          {analysedLine(analysed, labels)}
        </div>
      ) : null}
    </div>
  );
}

export function HoverCard({
  file,
  overlay,
  x,
  y,
  width,
  height,
}: {
  file: CodeHealthMapFile;
  overlay: CodeHealthOverlay;
  /** Pointer position, in container-relative px. */
  x: number;
  y: number;
  width: number;
  height: number;
}) {
  const cov = file.line_coverage_pct;
  const slash = file.file_path.lastIndexOf("/");
  const dir = slash < 0 ? "" : file.file_path.slice(0, slash + 1);
  const name = slash < 0 ? file.file_path : file.file_path.slice(slash + 1);
  return (
    <div
      data-testid="map-hover-card"
      // Sized to its content up to a ceiling, so a short name gets a small
      // card. The name is the answer to "what am I pointing at" and is never
      // truncated: it wraps instead, and the directory above it is the part
      // that gives way when the path is long.
      className={CARD}
      style={cardPosition(x, y, width, height)}
    >
      {dir ? (
        <div className="truncate font-mono text-[10px] leading-tight text-[var(--color-text-tertiary)]">
          {dir}
        </div>
      ) : null}
      <div className="break-all font-mono text-xs font-medium leading-snug text-[var(--color-text-primary)]">
        {name}
      </div>
      {overlay === "performance" ? (
        <div className="mt-0.5 text-xs leading-tight text-[var(--color-text-secondary)]">
          {performanceSentence(file)}
        </div>
      ) : null}
      <div className="mt-0.5 text-[10px] leading-tight text-[var(--color-text-tertiary)] tabular-nums">
        {file.score == null ? scoreText(file) : `score ${scoreText(file)}`} ·{" "}
        {file.nloc.toLocaleString()} NLOC
        {cov != null ? ` · ${Math.round(cov)}% cov` : ""}
        {file.has_test_file ? "" : " · untested"}
      </div>
    </div>
  );
}
