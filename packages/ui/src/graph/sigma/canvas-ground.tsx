"use client";

import { useEffect, useRef, useState } from "react";
import type Sigma from "sigma";

/** Dot spacing in graph units before the octave clamp below. */
const BASE_SPACING = 40;
/** Screen-pixel band the spacing is kept inside by doubling / halving. */
const MIN_PX = 22;
const MAX_PX = 88;
const DOT_RADIUS = 1.1;

/** `useId` output is not safe inside `url(#...)`: React 19 emits non-ASCII
 *  delimiters, which percent-encode before the SVG fragment lookup and leave
 *  the fill unresolved. A counter keeps the id ASCII and unique. */
let groundSeq = 0;

/**
 * Graph-paper ground under the canvas.
 *
 * Dark mode painted `--color-bg-root`, which is also the page body colour, so
 * canvas and page were the same value and the map read as a void. The plane is
 * right (the canvas is the subject); it just had nothing on it. Light mode gets
 * its ground for free from the warm paper root.
 *
 * Spacing doubles or halves to stay inside a screen-pixel band, so density
 * holds steady while the step itself gives the zoom feedback the canvas
 * otherwise has none of. An SVG pattern moved by one `patternTransform` write
 * per frame; the tile is only resized when the octave actually steps.
 */
export function CanvasGround({ sigma }: { sigma: Sigma | null }) {
  const patternRef = useRef<SVGPatternElement>(null);
  const circleRef = useRef<SVGCircleElement>(null);
  const [patternId] = useState(() => `canvas-ground-${(groundSeq += 1)}`);

  useEffect(() => {
    if (!sigma) return;
    const pattern = patternRef.current;
    const circle = circleRef.current;
    if (!pattern || !circle) return;

    let lastSpacing = 0;
    const draw = () => {
      const origin = sigma.graphToViewport({ x: 0, y: 0 });
      const unit = sigma.graphToViewport({ x: BASE_SPACING, y: 0 });
      let spacing = Math.hypot(unit.x - origin.x, unit.y - origin.y);
      if (!Number.isFinite(spacing) || spacing <= 0) return;
      while (spacing < MIN_PX) spacing *= 2;
      while (spacing > MAX_PX) spacing /= 2;

      // Panning only moves the lattice. Resizing the tile invalidates it and
      // re-rasterises the full-viewport fill, so only do that when the octave
      // actually steps.
      if (spacing !== lastSpacing) {
        lastSpacing = spacing;
        pattern.setAttribute("width", String(spacing));
        pattern.setAttribute("height", String(spacing));
        circle.setAttribute("cx", String(spacing / 2));
        circle.setAttribute("cy", String(spacing / 2));
      }
      pattern.setAttribute(
        "patternTransform",
        `translate(${origin.x % spacing},${origin.y % spacing})`,
      );
    };

    sigma.on("afterRender", draw);
    draw();
    return () => {
      sigma.off("afterRender", draw);
    };
  }, [sigma]);

  return (
    <svg className="pointer-events-none absolute inset-0 z-0 h-full w-full" aria-hidden="true">
      <defs>
        <pattern ref={patternRef} id={patternId} patternUnits="userSpaceOnUse" width={40} height={40}>
          <circle
            ref={circleRef}
            r={DOT_RADIUS}
            cx={20}
            cy={20}
            fill="var(--color-canvas-grid, rgba(128,128,128,0.1))"
          />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${patternId})`} />
    </svg>
  );
}
