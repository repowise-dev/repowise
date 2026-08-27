"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { CoverageBasis } from "@repowise-dev/types/health";

export interface RiskCoveragePoint {
  file_path: string;
  health_score: number;
  /** Measured basis only. */
  line_coverage_pct: number | null;
  nloc: number;
  /** Inferred basis only: does any test reach this file. */
  reached?: boolean;
}

export interface RiskCoverageScatterProps {
  points: RiskCoveragePoint[];
  onSelect?: (point: RiskCoveragePoint) => void;
  height?: number;
  /**
   * Which signal the points carry. `measured` plots line coverage on a 0-100
   * X axis; `inferred` collapses X to two positions. Defaults to `measured`,
   * which is what every existing caller passes nothing for.
   */
  basis?: CoverageBasis;
}

/**
 * Health × tests. Y is the defect-health score (0 to 10, higher is healthier) and
 * dot radius encodes lines of code on both bases. What X means depends on which
 * signal answered, and that is the whole design.
 *
 * On the **measured** basis X is line coverage, 0 to 100, and the plot reads as
 * four quadrants:
 *
 *   - Top right (healthy, covered):   Sweet spot
 *   - Top left  (healthy, uncovered): Risky, needs tests
 *   - Bottom right (weak, covered):   Tested but messy
 *   - Bottom left  (weak, uncovered): Critical hotspot
 *
 * On the **inferred** basis there is no percentage to plot — reaching is a
 * file-level fact with no line attribution — so X collapses to two positions:
 * nothing reaches this file, or something does. That is deliberate rather than
 * a degraded fallback. The chart's own resolution is the honest statement about
 * the evidence behind it: two columns read as coarser than a measurement
 * without a disclaimer nobody reads, ingesting a report becomes "the axis gains
 * focus" rather than a prerequisite, and a gradient that is never drawn cannot
 * be misread as one.
 *
 * Four things here are deliberate rather than incidental:
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
 *
 * The inferred columns are beeswarmed, and the jitter is **seeded off the file
 * path** rather than random. X carries one bit there, so horizontal space is
 * otherwise dead and a thousand files stack into two vertical lines that hide
 * the mass. Random jitter would move every dot on each re-render, which is the
 * same class of problem the memo boundary above exists for.
 */
export function RiskCoverageScatter({
  points,
  onSelect,
  height = 340,
  basis = "measured",
}: RiskCoverageScatterProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Seeded rather than zero so the first paint is a plausible chart instead of
  // an empty box that reflows a frame later.
  const [width, setWidth] = useState(900);
  const [hovered, setHovered] = useState<number | null>(null);
  const inferred = basis === "inferred";

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
      points.filter((p) =>
        inferred
          ? p.reached != null && Number.isFinite(p.health_score)
          : p.line_coverage_pct != null && Number.isFinite(p.health_score),
      ),
    [points, inferred],
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
    // Column centres and the half-width the swarm may spread into. Kept clear
    // of the divider so the two clouds never touch and the split stays legible
    // at any container width.
    const colLeft = padL + plotW * 0.25;
    const colRight = padL + plotW * 0.75;
    const swarm = plotW * 0.17;
    const xOf = (p: RiskCoveragePoint) => {
      if (!inferred) return xScale(p.line_coverage_pct ?? 0);
      const centre = p.reached ? colRight : colLeft;
      return centre + (hash01(p.file_path) * 2 - 1) * swarm;
    };
    return {
      W,
      H,
      padL,
      padR,
      padT,
      padB,
      plotW,
      xScale,
      yScale,
      radius,
      xOf,
      colLeft,
      colRight,
      // 60% coverage and a 7.0 score are the quadrant thresholds. On the
      // inferred basis the vertical one is the column divider instead.
      midX: inferred ? padL + plotW * 0.5 : xScale(60),
      midY: yScale(7),
    };
  }, [width, height, data, inferred]);

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
            cx={geom.xOf(p)}
            cy={geom.yScale(p.health_score)}
            r={geom.radius(p.nloc)}
            className={`${inferred ? "" : bandFill(p.health_score)} ${onSelect ? "cursor-pointer" : ""}`}
            {...(inferred ? { fill: reachedFill(p.reached) } : {})}
            fillOpacity={0.75}
          />
        ))}
      </g>
    ),
    [data, geom, onSelect],
  );

  if (data.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)]">
        {inferred
          ? "No file carries both a health score and a place in the dependency graph yet, so there is nothing to plot."
          : "No file carries both a health score and a coverage figure yet, so there is nothing to plot."}
      </p>
    );
  }

  const { W, H, padL, padR, padT, padB, midX, midY, xScale, yScale, xOf } = geom;
  const active = hovered != null ? data[hovered] : undefined;
  const reachedCount = inferred ? data.filter((p) => p.reached).length : 0;

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
          aria-label={
            inferred
              ? "Health against whether any test reaches the file, one dot per file"
              : "Health against line coverage, one dot per file"
          }
          onMouseOver={(e) => setHovered(indexFrom(e.target))}
          onMouseOut={() => setHovered(null)}
          onClick={(e) => {
            if (!onSelect) return;
            const i = indexFrom(e.target);
            const point = i == null ? undefined : data[i];
            if (point) onSelect(point);
          }}
        >
          {inferred ? null : (
            <>
              {/* Quadrant tinting. Faint enough to read as ground rather than as
                  four coloured panels the dots sit on top of. */}
              <rect x={padL} y={padT} width={midX - padL} height={midY - padT} fill="currentColor" className="text-[var(--color-warning)]/5" />
              <rect x={midX} y={padT} width={W - padR - midX} height={midY - padT} fill="currentColor" className="text-[var(--color-success)]/5" />
              <rect x={padL} y={midY} width={midX - padL} height={H - padB - midY} fill="currentColor" className="text-[var(--color-error)]/8" />
              <rect x={midX} y={midY} width={W - padR - midX} height={H - padB - midY} fill="currentColor" className="text-[var(--color-caution)]/5" />
            </>
          )}

          {/* Axes and the two thresholds */}
          <line x1={padL} y1={padT} x2={padL} y2={H - padB} stroke="currentColor" strokeOpacity={0.2} />
          <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="currentColor" strokeOpacity={0.2} />
          <line x1={midX} y1={padT} x2={midX} y2={H - padB} stroke="currentColor" strokeOpacity={0.1} strokeDasharray="3 3" />
          <line x1={padL} y1={midY} x2={W - padR} y2={midY} stroke="currentColor" strokeOpacity={0.1} strokeDasharray="3 3" />

          {inferred ? (
            <>
              <text x={geom.colLeft} y={H - padB + 15} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6}>
                no test reaches it
              </text>
              <text x={geom.colRight} y={H - padB + 15} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6}>
                a test reaches it
              </text>
            </>
          ) : (
            [0, 25, 50, 75, 100].map((v) => (
              <text key={`x${v}`} x={xScale(v)} y={H - padB + 15} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.5}>
                {v}%
              </text>
            ))
          )}
          {[0, 2, 4, 6, 8, 10].map((v) => (
            <text key={`y${v}`} x={padL - 7} y={yScale(v) + 3} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>
              {v}
            </text>
          ))}
          {!inferred && (
            <text x={padL + (W - padL - padR) / 2} y={H - 3} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6}>
              Line coverage
            </text>
          )}
          <text x={11} y={H / 2} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.6} transform={`rotate(-90 11 ${H / 2})`}>
            Health score
          </text>

          {/* Region captions. These stay on the canvas because they name a part
              of it; the chart's key, which does not, sits underneath. */}
          {inferred ? (
            <text x={padL + 8} y={H - padB - 8} fontSize={10} fill="currentColor" opacity={0.55}>
              Weak, and nothing runs it
            </text>
          ) : (
            <>
              <text x={padL + 8} y={padT + 14} fontSize={10} fill="currentColor" opacity={0.5}>Risky, needs tests</text>
              <text x={W - padR - 8} y={padT + 14} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>Sweet spot</text>
              <text x={padL + 8} y={H - padB - 8} fontSize={10} fill="currentColor" opacity={0.5}>Critical hotspot</text>
              <text x={W - padR - 8} y={H - padB - 8} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>Tested but messy</text>
            </>
          )}

          {field}

          {/* The hover ring is drawn over the field rather than by re-rendering
              the hovered dot, which would take the whole field with it. */}
          {active && (
            <circle
              cx={xOf(active)}
              cy={yScale(active.health_score)}
              r={geom.radius(active.nloc) * 1.5}
              className={inferred ? "" : bandFill(active.health_score)}
              {...(inferred ? { fill: reachedFill(active.reached) } : {})}
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
              left: Math.min(Math.max(xOf(active), 110), W - 110),
              top: yScale(active.health_score) - 10,
            }}
          >
            <span className="block truncate font-mono text-[var(--color-text-primary)]">
              {active.file_path}
            </span>
            <span className="tabular-nums text-[var(--color-text-tertiary)]">
              {active.health_score.toFixed(1)} health ·{" "}
              {inferred
                ? active.reached
                  ? "a test reaches it"
                  : "no test reaches it"
                : `${active.line_coverage_pct?.toFixed(0)}% covered`}{" "}
              · {active.nloc} lines
            </span>
          </div>
        )}
      </div>

      <p className="border-t border-[var(--color-border-default)] pt-2 font-mono text-[10px] uppercase tracking-[0.12em] tabular-nums text-[var(--color-text-tertiary)]">
        {inferred
          ? `${data.length.toLocaleString()} files · ${reachedCount.toLocaleString()} reached by a test · colour repeats the column · dot size = lines of code · height is the health score · horizontal spread carries no meaning`
          : `${data.length.toLocaleString()} files · dot size = lines of code · thresholds at 60% coverage and 7.0 health`}
      </p>
    </div>
  );
}

/**
 * Fill by which column the file sits in, on the inferred basis.
 *
 * The sunset pair, and deliberately not the health ramp. Green/amber/red carry a
 * band, so spending them here would dress a static reading as a measurement -
 * and it would be redundant besides, because the Y axis already *is* the health
 * score. Hue is the only thing left to carry the split, which is the one fact
 * this chart exists to show.
 *
 * Tried first as a 6% wash behind the dots, which failed twice over: plum at
 * that opacity is indistinguishable from grey on a light ground, and the
 * health-banded dots on top took the whole visual budget to re-say what their
 * own vertical position already said.
 */
function reachedFill(reached: boolean | undefined): string {
  return reached ? "var(--color-accent-fill)" : "var(--color-accent-secondary)";
}

/** Fill by health band. The bands are the same ones the rest of health uses. */
function bandFill(score: number): string {
  if (score < 4) return "fill-[var(--color-error)]";
  if (score < 6) return "fill-[var(--color-warning)]";
  if (score < 8) return "fill-[var(--color-caution)]";
  return "fill-[var(--color-success)]";
}

/**
 * A stable number in [0, 1) from a file path, for the beeswarm offset.
 *
 * FNV-1a, which is a few lines and has no dependency. The requirement is only
 * that it spreads evenly and returns the same value for the same path every
 * render; the offset is decoration, so nothing rests on its distribution beyond
 * looking unclustered.
 */
function hash01(path: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < path.length; i++) {
    h ^= path.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return ((h >>> 0) % 100000) / 100000;
}
