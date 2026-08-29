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
 * Pointer-bound decoding, in the active lens's terms.
 *
 * Hover answers "what is this and what does the current lens say about it".
 * The lens's own sentence leads, so the card and the inspector describe the
 * same object the same way.
 */
export function HoverCard({
  file,
  overlay,
}: {
  file: CodeHealthMapFile;
  overlay: CodeHealthOverlay;
}) {
  const cov = file.line_coverage_pct;
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 max-w-[75%] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1.5 text-xs shadow-md">
      <div className="truncate font-mono text-[var(--color-text-primary)]">
        {file.file_path}
      </div>
      {overlay === "performance" ? (
        <div className="text-[var(--color-text-secondary)]">
          {performanceSentence(file)}
        </div>
      ) : null}
      <div className="text-[var(--color-text-tertiary)] tabular-nums">
        score {file.score.toFixed(1)} · {file.nloc.toLocaleString()} NLOC
        {cov != null ? ` · ${Math.round(cov)}% cov` : ""}
        {file.has_test_file ? "" : " · untested"}
      </div>
    </div>
  );
}
