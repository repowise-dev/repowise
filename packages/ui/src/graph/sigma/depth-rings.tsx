"use client";

import { useEffect, useRef } from "react";
import type Sigma from "sigma";
import { resolveToken, useThemeVersion } from "../../shared/use-theme-tokens";

interface DepthRingsProps {
  sigma: Sigma | null;
  /** Ring radii in *graph* coordinates (from computeRadialLayout). */
  ringRadii: readonly [number, number, number] | null;
}

/**
 * Faint concentric "depth" rings drawn behind the constellation. An SVG
 * underlay synced to the Sigma camera: we project the graph origin and a
 * point one radius out into viewport pixels every frame, so the rings track
 * zoom/pan smoothly with transform-only updates (no re-layout). Captioned
 * only in the legend ("inner = entry surface"), never on-canvas.
 */
export function DepthRings({ sigma, ringRadii }: DepthRingsProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const circleRefs = useRef<(SVGCircleElement | null)[]>([]);
  const themeVersion = useThemeVersion();

  useEffect(() => {
    if (!sigma || !ringRadii) return;
    const svg = svgRef.current;
    if (!svg) return;

    const stroke = resolveToken("--color-canvas-dot", "rgba(128,128,128,0.08)");

    const draw = () => {
      const center = sigma.graphToViewport({ x: 0, y: 0 });
      // Project a point one unit-radius along +x to get the pixel scale.
      const edge = sigma.graphToViewport({ x: ringRadii[0], y: 0 });
      const pxPerRing = Math.hypot(edge.x - center.x, edge.y - center.y);
      ringRadii.forEach((_, i) => {
        const c = circleRefs.current[i];
        if (!c) return;
        c.setAttribute("cx", String(center.x));
        c.setAttribute("cy", String(center.y));
        c.setAttribute("r", String(pxPerRing * (i + 1)));
        c.setAttribute("stroke", stroke);
      });
    };

    sigma.on("afterRender", draw);
    draw();
    return () => {
      sigma.off("afterRender", draw);
    };
  }, [sigma, ringRadii, themeVersion]);

  if (!ringRadii) return null;

  return (
    <svg
      ref={svgRef}
      className="absolute inset-0 w-full h-full pointer-events-none z-0"
      aria-hidden="true"
    >
      {[0, 1, 2].map((i) => (
        <circle
          key={i}
          ref={(el) => {
            circleRefs.current[i] = el;
          }}
          cx={0}
          cy={0}
          r={0}
          fill="none"
          strokeWidth={1}
        />
      ))}
    </svg>
  );
}
