"use client";

/**
 * The base field: one circle per file, and the performance lens's rings.
 *
 * Both layers are memoized and fed only stable props, so hover, selection and
 * search - which all re-render the facade - never reconcile these elements.
 * Everything that moves with the pointer lives in the overlay layer instead.
 */

import { memo } from "react";
import { pressureRing } from "./lens";
import type { CodeHealthMapFile, Galaxy } from "./types";

export interface NodeLayerProps {
  galaxies: Galaxy[];
  focusModuleKey: string | null;
  fill: (f: CodeHealthMapFile) => string;
  offX: number;
  offY: number;
  /**
   * Stroke in user units, pre-divided by the zoom scale so it lands on half a
   * device pixel at rest.
   *
   * The obvious spelling is `vectorEffect="non-scaling-stroke"`, which is what
   * this used. Chrome recomputes stroke geometry per element per frame for
   * that attribute, and these are thousands of elements inside the group whose
   * transform animates on every galaxy zoom. Dividing by the scale is free.
   * The trade is that during the transition the browser interpolates the
   * transform while this value is already at its destination, so strokes are
   * fractionally off mid-flight, which is invisible on a half-pixel line under
   * a moving field and correct the instant it settles.
   */
  strokeWidth: number;
  interactive: boolean;
  onSelect: (path: string) => void;
  onHoverEnter: (f: CodeHealthMapFile) => void;
  onHoverLeave: (f: CodeHealthMapFile) => void;
}

export const FileNodes = memo(function FileNodes({
  galaxies,
  focusModuleKey,
  fill,
  offX,
  offY,
  strokeWidth,
  interactive,
  onSelect,
  onHoverEnter,
  onHoverLeave,
}: NodeLayerProps) {
  return (
    <>
      {galaxies.map((g) => {
        const faded = focusModuleKey != null && g.module !== focusModuleKey;
        return (
          <g key={`nodes-${g.module}`}>
            {g.nodes.map((nd) => {
              const f = nd.file;
              return (
                /* No <title> child. It was thousands of extra DOM nodes driving
                   a native tooltip that appears on its own delay, in the corner
                   the pointer is in, restating what the hover card already
                   shows the moment you touch a node - two tooltips racing.
                   `data-path` costs no node and gives tests a stable handle. */
                <circle
                  key={f.file_path}
                  data-path={f.file_path}
                  cx={nd.x + offX}
                  cy={nd.y + offY}
                  r={nd.r}
                  fill={fill(f)}
                  fillOpacity={faded ? 0.18 : 0.9}
                  stroke="var(--color-bg-root)"
                  strokeWidth={strokeWidth}
                  className={interactive ? "cursor-pointer" : undefined}
                  onMouseEnter={() => onHoverEnter(f)}
                  onMouseLeave={() => onHoverLeave(f)}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelect(f.file_path);
                  }}
                />
              );
            })}
          </g>
        );
      })}
    </>
  );
});

/**
 * The performance lens's second channel, as its own layer.
 *
 * Only files carrying an open cause get a ring, so this is a fraction of the
 * field rather than a mark on every node: on a repository of two thousand
 * drawn files a few hundred draw one. Width and colour step with the burden,
 * the dash pattern says what could be done about it, and the key spells both
 * out in words. The ring never claims a runtime magnitude.
 */
export const PressureRings = memo(function PressureRings({
  galaxies,
  focusModuleKey,
  offX,
  offY,
  scale,
}: {
  galaxies: Galaxy[];
  focusModuleKey: string | null;
  offX: number;
  offY: number;
  /** Zoom scale, so stroke widths land where they were designed. */
  scale: number;
}) {
  return (
    <g className="pointer-events-none">
      {galaxies.map((g) => {
        const faded = focusModuleKey != null && g.module !== focusModuleKey;
        return (
          <g key={`rings-${g.module}`} opacity={faded ? 0.2 : 1}>
            {g.nodes.map((nd) => {
              const ring = pressureRing(nd.file);
              if (!ring) return null;
              const width = ring.width / scale;
              // The pattern is in user units too, so it has to shrink with the
              // stroke or a zoomed galaxy turns every dashed ring solid.
              const dash = ring.dash
                ? ring.dash
                    .split(" ")
                    .map((n) => Number(n) / scale)
                    .join(" ")
                : undefined;
              return (
                <circle
                  key={nd.file.file_path}
                  data-ring={nd.file.file_path}
                  cx={nd.x + offX}
                  cy={nd.y + offY}
                  // Outside the node, by half the stroke plus a hairline, so
                  // the ring reads as pressure around the file rather than as
                  // a border on it.
                  r={nd.r + width}
                  fill="none"
                  stroke={ring.stroke}
                  strokeWidth={width}
                  {...(dash ? { strokeDasharray: dash } : {})}
                />
              );
            })}
          </g>
        );
      })}
    </g>
  );
});
