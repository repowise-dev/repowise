"use client";

/**
 * The base field: one circle per file, and nothing else.
 *
 * Memoized and fed only stable props, so hover, selection and search - which
 * all re-render the facade - never reconcile these thousands of elements.
 * Everything that moves with the pointer lives in the overlay layer instead.
 *
 * There is deliberately no second mark layer here. A rare per-file annotation
 * was tried both ways, as a ring outside the node and as a core inside it, and
 * neither survived contact with the field: at this density an outline lands on
 * the neighbours and reads as a mark on the group, and a core in the one
 * colour with enough contrast to be seen is louder than the severity ramp it
 * is annotating. Anything that fine belongs in the words beside the map.
 */

import { memo } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
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
  /** The event comes along so the hover card can open at the pointer. */
  onHoverEnter: (f: CodeHealthMapFile, e: ReactMouseEvent) => void;
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
                  // One opacity for every lens. Opacity here means "this galaxy
                  // is not the focused one" and nothing else; a lens that also
                  // spent it on data left the reader unable to tell a quiet
                  // file from a dimmed one.
                  fillOpacity={faded ? 0.18 : 0.9}
                  // The separating stroke is what makes this a field of files
                  // rather than a wash. Every node keeps it, whatever it is
                  // filled with.
                  stroke="var(--color-bg-root)"
                  strokeWidth={strokeWidth}
                  className={interactive ? "cursor-pointer" : undefined}
                  onMouseEnter={(e) => onHoverEnter(f, e)}
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
