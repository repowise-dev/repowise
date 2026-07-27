"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export interface RiskCoveragePoint {
  file_path: string;
  health_score: number;
  line_coverage_pct: number | null;
  nloc: number;
}

export interface RiskCoverageScatterProps {
  points: RiskCoveragePoint[];
  onSelect?: (point: RiskCoveragePoint) => void;
  height?: number;
}

/**
 * Health × coverage quadrant plot. Y axis is the defect-risk score (0 to 10,
 * higher is better), X axis is line coverage (0 to 100), and dot radius encodes
 * lines of code.
 *
 *   - Top right (healthy, covered):   Sweet spot
 *   - Top left  (healthy, uncovered): Risky, needs tests
 *   - Bottom right (weak, covered):   Tested but messy
 *   - Bottom left  (weak, uncovered): Critical hotspot
 *
 * Three things here are deliberate rather than incidental:
 *
 * The SVG sizes to its container instead of scaling a fixed 640-unit viewBox.
 * Inside a full-width section that box was being scaled by ~2.4, so every 10px
 * axis label rendered at 24px and the quadrant captions outweighed the field
 * they annotate. One unit is now one CSS pixel at every width, which also makes
 * the tooltip's position a plain read of the point's coordinates.
 *
 * The dot field is memoised away from the hover state. It is one element per
 * file and a real repo brings ~1,400 of them; without the split, moving the
 * pointer reconciled the whole field on every event.
 *
 * No `<title>` child per dot. That is a second DOM node per file for a native
 * tooltip that fires on its own delay and fights the hover card below. The path
 * rides on `data-file` instead, which is also a stable handle for tests.
 */
export function RiskCoverageScatter({
  points,
  onSelect,
  height = 340,
}: RiskCoverageScatterProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Seeded rather than zero so the first paint is a plausible chart instead of
  // an empty box that reflows a frame later.
  const [width, setWidth] = useState(900);
  const [hovered, setHovered] = useState<number | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setWidth(w);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const data = useMemo(
    () =>
      points.filter(
        (p) => p.line_coverage_pct != null && Number.isFinite(p.health_score),
      ),
    [points],
  );

  const geom = useMemo(() => {
    const W = Math.max(320, Math.round(width));
    const H = height;
    const padL = 36;
    const padR = 16;
    const padT = 22;
    const padB = 32;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const maxNloc = Math.max(...data.map((d) => d.nloc), 1);
    const xScale = (pct: number) => padL + (pct / 100) * plotW;
    const yScale = (score: number) => padT + ((10 - score) / 10) * plotH;
    const radius = (nloc: number) => 2 + Math.min(7, Math.sqrt(nloc / maxNloc) * 7);
    return {
      W,
      H,
      padL,
      padR,
      padT,
      padB,
      xScale,
      yScale,
      radius,
      // 60% coverage and a 7.0 score are the quadrant thresholds.
      midX: xScale(60),
      midY: yScale(7),
    };
  }, [width, height, data]);

  // The expensive part, held apart from `hovered` so pointer movement does not
  // rebuild one element per file. React bails out of reconciling a subtree whose
  // element is referentially identical, so this is what keeps hover cheap.
  const field = useMemo(
    () => (
      <g>
        {data.map((p, i) => (
          <circle
            key={p.file_path}
            data-file={p.file_path}
            data-i={i}
            cx={geom.xScale(p.line_coverage_pct ?? 0)}
            cy={geom.yScale(p.health_score)}
            r={geom.radius(p.nloc)}
            className={`${bandFill(p.health_score)} ${onSelect ? "cursor-pointer" : ""}`}
            fillOpacity={0.7}
          />
        ))}
      </g>
    ),
    [data, geom, onSelect],
  );

  if (data.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)]">
        No file carries both a health score and a coverage figure yet, so there is
        nothing to plot.
      </p>
    );
  }

  const { W, H, padL, padR, padT, padB, midX, midY, xScale, yScale } = geom;
  const active = hovered != null ? data[hovered] : undefined;

  // Delegated: one handler on the svg rather than two props on every dot.
  const indexFrom = (target: EventTarget): number | null => {
    const raw = (target as SVGElement)?.getAttribute?.("data-i");
    return raw == null ? null : Number(raw);
  };

  return (
    <div className="flex flex-col gap-2.5">
      <div ref={containerRef} className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          height={H}
          role="img"
          aria-label="Health against line coverage, one dot per file"
          onMouseOver={(e) => setHovered(indexFrom(e.target))}
          onMouseOut={() => setHovered(null)}
          onClick={(e) => {
            if (!onSelect) return;
            const i = indexFrom(e.target);
            const point = i == null ? undefined : data[i];
            if (point) onSelect(point);
          }}
        >
          {/* Quadrant tinting. Faint enough to read as ground rather than as
              four coloured panels the dots sit on top of. */}
          <rect x={padL} y={padT} width={midX - padL} height={midY - padT} fill="currentColor" className="text-[var(--color-warning)]/5" />
          <rect x={midX} y={padT} width={W - padR - midX} height={midY - padT} fill="currentColor" className="text-[var(--color-success)]/5" />
          <rect x={padL} y={midY} width={midX - padL} height={H - padB - midY} fill="currentColor" className="text-[var(--color-error)]/8" />
          <rect x={midX} y={midY} width={W - padR - midX} height={H - padB - midY} fill="currentColor" className="text-[var(--color-caution)]/5" />

          {/* Axes and the two thresholds */}
          <line x1={padL} y1={padT} x2={padL} y2={H - padB} stroke="currentColor" strokeOpacity={0.2} />
          <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="currentColor" strokeOpacity={0.2} />
          <line x1={midX} y1={padT} x2={midX} y2={H - padB} stroke="currentColor" strokeOpacity={0.1} strokeDasharray="3 3" />
          <line x1={padL} y1={midY} x2={W - padR} y2={midY} stroke="currentColor" strokeOpacity={0.1} strokeDasharray="3 3" />

          {[0, 25, 50, 75, 100].map((v) => (
            <text key={`x${v}`} x={xScale(v)} y={H - padB + 15} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.5}>
              {v}%
            </text>
          ))}
          {[0, 2, 4, 6, 8, 10].map((v) => (
            <text key={`y${v}`} x={padL - 7} y={yScale(v) + 3} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>
              {v}
            </text>
          ))}
          <text x={padL + (W - padL - padR) / 2} y={H - 3} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6}>
            Line coverage
          </text>
          <text x={11} y={H / 2} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6} transform={`rotate(-90 11 ${H / 2})`}>
            Health score
          </text>

          {/* Quadrant captions. These stay on the canvas because they name a
              region of it; the chart's key, which does not, sits underneath. */}
          <text x={padL + 8} y={padT + 14} fontSize={10} fill="currentColor" opacity={0.5}>Risky, needs tests</text>
          <text x={W - padR - 8} y={padT + 14} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>Sweet spot</text>
          <text x={padL + 8} y={H - padB - 8} fontSize={10} fill="currentColor" opacity={0.5}>Critical hotspot</text>
          <text x={W - padR - 8} y={H - padB - 8} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>Tested but messy</text>

          {field}

          {/* The hover ring is drawn over the field rather than by re-rendering
              the hovered dot, which would take the whole field with it. */}
          {active && (
            <circle
              cx={xScale(active.line_coverage_pct ?? 0)}
              cy={yScale(active.health_score)}
              r={geom.radius(active.nloc) * 1.5}
              className={bandFill(active.health_score)}
              fillOpacity={0.9}
              stroke="var(--color-text-primary)"
              strokeWidth={1.5}
              pointerEvents="none"
            />
          )}
        </svg>

        {active && (
          <div
            className="pointer-events-none absolute z-10 max-w-[min(320px,80%)] -translate-x-1/2 -translate-y-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-xs shadow-md"
            style={{
              // Clamped so a dot against either edge does not push the card out
              // of the plot; the offset lifts it clear of its own dot.
              left: Math.min(Math.max(xScale(active.line_coverage_pct ?? 0), 110), W - 110),
              top: yScale(active.health_score) - 10,
            }}
          >
            <span className="block truncate font-mono text-[var(--color-text-primary)]">
              {active.file_path}
            </span>
            <span className="tabular-nums text-[var(--color-text-tertiary)]">
              {active.health_score.toFixed(1)} health ·{" "}
              {active.line_coverage_pct?.toFixed(0)}% covered · {active.nloc} lines
            </span>
          </div>
        )}
      </div>

      <p className="border-t border-[var(--color-border-default)] pt-2 font-mono text-[10px] uppercase tracking-[0.12em] tabular-nums text-[var(--color-text-tertiary)]">
        {data.length.toLocaleString()} files · dot size = lines of code · thresholds
        at 60% coverage and 7.0 health
      </p>
    </div>
  );
}

/** Fill by health band. The bands are the same ones the rest of health uses. */
function bandFill(score: number): string {
  if (score < 4) return "fill-[var(--color-error)]";
  if (score < 6) return "fill-[var(--color-warning)]";
  if (score < 8) return "fill-[var(--color-caution)]";
  return "fill-[var(--color-success)]";
}
