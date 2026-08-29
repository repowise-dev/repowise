"use client";

/**
 * Everything that follows the pointer, the keyboard, or the search box.
 *
 * Kept off the base field on purpose. Hovering a node used to re-render the
 * whole node layer, and typing a search query used to re-render it once per
 * keystroke, because the query dimmed every non-matching circle. Both are now
 * bounded marks drawn on top: hover paints one circle, and search paints a
 * ring on the matches instead of touching the thousands that did not match.
 */

import { performanceSentence } from "./lens";
import type { CodeHealthMapFile, CodeHealthOverlay, FileNode } from "./types";

/** Match rings drawn at once. Beyond this the field is the answer, not a mark. */
export const SEARCH_MARK_CAP = 250;

/** The hovered and selected nodes only, drawn on top of the static field. */
export function NodeHighlight({
  hovered,
  selectedPath,
  nodeIndex,
  offX,
  offY,
  fill,
}: {
  hovered: CodeHealthMapFile | null;
  selectedPath: string | null;
  nodeIndex: Map<string, FileNode>;
  offX: number;
  offY: number;
  fill: (f: CodeHealthMapFile) => string;
}) {
  const sel = selectedPath ? nodeIndex.get(selectedPath) : null;
  const hov = hovered ? nodeIndex.get(hovered.file_path) : null;
  return (
    <g className="pointer-events-none">
      {sel ? (
        <circle
          data-selected={sel.file.file_path}
          cx={sel.x + offX}
          cy={sel.y + offY}
          r={sel.r}
          fill={fill(sel.file)}
          fillOpacity={1}
          stroke="var(--color-accent-primary)"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
      {hov && hov !== sel ? (
        <circle
          cx={hov.x + offX}
          cy={hov.y + offY}
          r={hov.r + 1.5}
          fill={fill(hov.file)}
          fillOpacity={1}
          stroke="var(--color-bg-root)"
          strokeWidth={0.5}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
    </g>
  );
}

/**
 * A ring on every node whose path matches the query, bounded.
 *
 * Marking the matches rather than dimming the rest is what keeps typing off
 * the base layer: the number of elements this draws tracks the answer, not the
 * repository, and the field underneath is untouched.
 */
export function SearchMatches({
  paths,
  nodeIndex,
  offX,
  offY,
}: {
  paths: string[];
  nodeIndex: Map<string, FileNode>;
  offX: number;
  offY: number;
}) {
  if (paths.length === 0) return null;
  return (
    <g className="pointer-events-none">
      {paths.slice(0, SEARCH_MARK_CAP).map((path) => {
        const nd = nodeIndex.get(path);
        if (!nd) return null;
        return (
          <circle
            key={path}
            data-match={path}
            cx={nd.x + offX}
            cy={nd.y + offY}
            r={nd.r + 2.5}
            fill="none"
            stroke="var(--color-accent-primary)"
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
          />
        );
      })}
    </g>
  );
}

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
  const left = x + CARD_GAP + CARD_W > width ? x - CARD_GAP - CARD_W : x + CARD_GAP;
  const top = y + CARD_GAP + CARD_H > height ? y - CARD_GAP - CARD_H : y + CARD_GAP;
  return (
    <div
      data-testid="map-hover-card"
      // Sized to its content up to a ceiling, so a short name gets a small
      // card. The name is the answer to "what am I pointing at" and is never
      // truncated: it wraps instead, and the directory above it is the part
      // that gives way when the path is long.
      className="pointer-events-none absolute z-20 w-max max-w-[300px] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1.5 shadow-md"
      style={{ left: Math.max(0, left), top: Math.max(0, top) }}
    >
      {dir ? (
        <div className="truncate font-mono text-[10px] leading-tight text-[var(--color-text-tertiary)]">
          {dir}
        </div>
      ) : null}
      <div className="break-all font-mono text-[11px] font-medium leading-snug text-[var(--color-text-primary)]">
        {name}
      </div>
      {overlay === "performance" ? (
        <div className="mt-0.5 text-[11px] leading-tight text-[var(--color-text-secondary)]">
          {performanceSentence(file)}
        </div>
      ) : null}
      <div className="mt-0.5 text-[10px] leading-tight text-[var(--color-text-tertiary)] tabular-nums">
        score {file.score.toFixed(1)} · {file.nloc.toLocaleString()} NLOC
        {cov != null ? ` · ${Math.round(cov)}% cov` : ""}
        {file.has_test_file ? "" : " · untested"}
      </div>
    </div>
  );
}
