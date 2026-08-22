"use client";

/**
 * The structural plans as a field: file size against how much imports the file.
 *
 * This replaces a priority-by-effort quadrant that failed its own test. On a
 * real index 89% of plans are rated small effort, so the effort axis put almost
 * everything in one column and three of the four quadrant labels annotated
 * empty space; it also plotted 120 dots of 1,819 and said so only in a caption
 * inside the canvas. Restricting the field to the structural plans is what
 * makes a scatter honest here — those spread across both axes (dependents 1 to
 * 195, sizes 19 to 1,417 lines on this repo), and there are few enough to draw
 * every one.
 *
 * Three things it does that the quadrant did not, all from the design language:
 *
 * - **Type is a shape, not a hue.** Six `--color-refactor-*` accents separated
 *   categories where two types are 96% of the data, and one of them was the
 *   same green as the health badge beside it.
 * - **The viewBox tracks the container.** A fixed `0 0 1100 400` at
 *   `width:100%` scales its own text: 12px labels render at 4px on a phone and
 *   at 14px on a wide desktop. One unit is one CSS pixel at every width here.
 * - **Chrome goes around it.** The key, the counts and the bound live in a
 *   hairline row underneath; only the hover card tracks the pointer, and no
 *   `<title>` child fights it for the tooltip.
 */

import * as React from "react";

import { formatNumber } from "../lib/format";
import { typeMeta } from "./meta";
import { planPoint } from "./types";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

export interface StructuralMapProps {
  /** Structural plans. Those without both figures are dropped and counted. */
  plans: RefactoringPlan[];
  onSelect?: ((plan: RefactoringPlan) => void) | undefined;
  /** Lit from the row list, so hovering either half highlights the other. */
  highlightedId?: string | null | undefined;
  onHighlight?: ((id: string | null) => void) | undefined;
}

const PAD_L = 52;
const PAD_R = 24;
const PAD_T = 18;
const PAD_B = 46;

/** Marks are ~11px across, so the pointer needs a target bigger than the ink. */
const HIT_RADIUS = 12;

/** A deterministic sub-pixel offset, so two plans on one file do not stack into
 *  a single mark. Two detectors firing on the same file is common: a file big
 *  enough to split is often also in a cycle. */
function jitter(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0;
  return ((h % 9) - 4) * 1.1;
}

function markPath(type: string, cx: number, cy: number, r: number): string | null {
  if (type === "break_cycle") {
    return `M${cx} ${cy - r * 1.15}L${cx + r * 1.15} ${cy}L${cx} ${cy + r * 1.15}L${cx - r * 1.15} ${cy}Z`;
  }
  if (type === "extract_class" || type === "move_method") {
    return `M${cx} ${cy - r * 1.25}L${cx + r * 1.2} ${cy + r * 0.95}L${cx - r * 1.2} ${cy + r * 0.95}Z`;
  }
  return null; // split_file draws as a circle
}

/** Nice round ceilings so the top gridline is a number a person would say. */
function axisCeiling(max: number): number {
  const step = max > 800 ? 400 : max > 200 ? 100 : max > 40 ? 20 : 5;
  return Math.max(step, Math.ceil(max / step) * step);
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0;
}

export function StructuralMap({
  plans,
  onSelect,
  highlightedId,
  onHighlight,
}: StructuralMapProps) {
  const wrapRef = React.useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = React.useState(0);

  React.useEffect(() => {
    const node = wrapRef.current;
    if (!node) return;
    const measure = () => setWidth(node.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const ro = new ResizeObserver(measure);
    ro.observe(node);
    return () => ro.disconnect();
  }, []);

  const points = React.useMemo(
    () =>
      plans
        .map((plan) => {
          const point = planPoint(plan);
          return point ? { plan, ...point } : null;
        })
        .filter((p): p is { plan: RefactoringPlan; x: number; y: number } => p !== null),
    [plans],
  );

  const dropped = plans.length - points.length;

  const geometry = React.useMemo(() => {
    if (points.length === 0 || width === 0) return null;
    const W = width;
    const H = Math.max(320, Math.min(460, Math.round(W * 0.44)));
    const plotW = W - PAD_L - PAD_R;
    const plotH = H - PAD_T - PAD_B;

    const xMax = Math.max(...points.map((p) => p.x));
    const yMax = axisCeiling(Math.max(...points.map((p) => p.y)));
    // Dependents are heavily right-skewed — a median of 8 against a max of 195
    // on this repo — so a linear axis crushes most of the field into the left
    // fifth. Log spreads it without hiding the outlier.
    const logMax = Math.log(Math.max(xMax, 2));
    const xScale = (v: number) => PAD_L + (Math.log(Math.max(v, 1)) / logMax) * plotW;
    const yScale = (v: number) => PAD_T + plotH - (v / yMax) * plotH;

    const xTicks = [1, 3, 10, 30, 100, 300, 1000].filter((t) => t <= xMax);
    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round((yMax * f) / 10) * 10);

    return {
      W,
      H,
      plotW,
      plotH,
      xScale,
      yScale,
      xTicks,
      yTicks,
      medianX: median(points.map((p) => p.x)),
      medianY: median(points.map((p) => p.y)),
    };
  }, [points, width]);

  const hovered = points.find((p) => p.plan.id === highlightedId) ?? null;

  const byType = new Map<string, number>();
  for (const p of points) byType.set(p.plan.refactoring_type, (byType.get(p.plan.refactoring_type) ?? 0) + 1);

  return (
    <div>
      <div ref={wrapRef} className="relative">
        {geometry ? (
          <svg
            viewBox={`0 0 ${geometry.W} ${geometry.H}`}
            height={geometry.H}
            className="block w-full text-[var(--color-text-primary)]"
            role="img"
            aria-label={`${points.length} structural refactoring plans plotted on file size against the number of files that import them. The ranked list below carries the same plans.`}
          >
            {geometry.yTicks.map((t) => (
              <g key={`y-${t}`}>
                <line
                  x1={PAD_L}
                  y1={geometry.yScale(t)}
                  x2={PAD_L + geometry.plotW}
                  y2={geometry.yScale(t)}
                  stroke="currentColor"
                  strokeOpacity={0.08}
                />
                <text
                  x={PAD_L - 9}
                  y={geometry.yScale(t) + 3.5}
                  textAnchor="end"
                  className="fill-[var(--color-text-tertiary)] font-mono text-[10px] tabular-nums"
                >
                  {formatNumber(t)}
                </text>
              </g>
            ))}

            {geometry.xTicks.map((t) => (
              <text
                key={`x-${t}`}
                x={geometry.xScale(t)}
                y={PAD_T + geometry.plotH + 18}
                textAnchor="middle"
                className="fill-[var(--color-text-tertiary)] font-mono text-[10px] tabular-nums"
              >
                {t}
              </text>
            ))}

            {/* Medians, so a reader can place a point without reading both axes. */}
            <line
              x1={geometry.xScale(geometry.medianX)}
              y1={PAD_T}
              x2={geometry.xScale(geometry.medianX)}
              y2={PAD_T + geometry.plotH}
              stroke="currentColor"
              strokeOpacity={0.2}
              strokeDasharray="4 4"
            />
            <line
              x1={PAD_L}
              y1={geometry.yScale(geometry.medianY)}
              x2={PAD_L + geometry.plotW}
              y2={geometry.yScale(geometry.medianY)}
              stroke="currentColor"
              strokeOpacity={0.2}
              strokeDasharray="4 4"
            />

            <text
              x={PAD_L + geometry.plotW / 2}
              y={geometry.H - 8}
              textAnchor="middle"
              className="fill-[var(--color-text-tertiary)] text-[11px]"
            >
              Files that import it
            </text>
            <text
              x={14}
              y={PAD_T + geometry.plotH / 2}
              textAnchor="middle"
              transform={`rotate(-90 14 ${PAD_T + geometry.plotH / 2})`}
              className="fill-[var(--color-text-tertiary)] text-[11px]"
            >
              Lines in the file
            </text>

            {points.map(({ plan, x, y }) => {
              const cx = geometry.xScale(x) + jitter(plan.id);
              const cy = geometry.yScale(y);
              const lit = plan.id === highlightedId;
              const r = lit ? 7 : 5.2;
              const path = markPath(plan.refactoring_type, cx, cy, r);
              const meta = typeMeta(plan.refactoring_type);
              return (
                <g
                  key={plan.id}
                  role={onSelect ? "button" : undefined}
                  tabIndex={onSelect ? 0 : undefined}
                  aria-label={`${meta.label}, ${plan.file_path}, ${formatNumber(x)} dependent files, ${formatNumber(y)} lines`}
                  className={onSelect ? "cursor-pointer outline-none" : "outline-none"}
                  onMouseEnter={() => onHighlight?.(plan.id)}
                  onMouseLeave={() => onHighlight?.(null)}
                  onFocus={() => onHighlight?.(plan.id)}
                  onBlur={() => onHighlight?.(null)}
                  onClick={onSelect ? () => onSelect(plan) : undefined}
                  onKeyDown={
                    onSelect
                      ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onSelect(plan);
                          }
                        }
                      : undefined
                  }
                >
                  {path ? (
                    <path
                      d={path}
                      fill="var(--color-accent-fill)"
                      fillOpacity={lit ? 0.95 : 0.72}
                    />
                  ) : (
                    <circle
                      cx={cx}
                      cy={cy}
                      r={r}
                      fill="var(--color-accent-fill)"
                      fillOpacity={lit ? 0.95 : 0.72}
                    />
                  )}
                  {lit ? (
                    <circle
                      cx={cx}
                      cy={cy}
                      r={r + 4}
                      fill="none"
                      stroke="var(--color-accent-fill)"
                      strokeOpacity={0.5}
                    />
                  ) : null}
                  <circle cx={cx} cy={cy} r={HIT_RADIUS} fill="transparent" />
                </g>
              );
            })}
          </svg>
        ) : (
          <div style={{ height: 320 }} />
        )}

        {hovered && geometry ? (
          <MapCard
            plan={hovered.plan}
            x={geometry.xScale(hovered.x)}
            y={geometry.yScale(hovered.y)}
            width={geometry.W}
          />
        ) : null}
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-[var(--color-border-default)] pt-3">
        {[...byType.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([type, count]) => (
            <span
              key={type}
              className="inline-flex items-center gap-2 text-xs text-[var(--color-text-secondary)]"
            >
              <svg width="13" height="13" aria-hidden className="shrink-0">
                {markPath(type, 6.5, 6.5, 4.6) ? (
                  <path d={markPath(type, 6.5, 6.5, 4.6)!} fill="var(--color-accent-fill)" fillOpacity={0.8} />
                ) : (
                  <circle cx="6.5" cy="6.5" r="4.6" fill="var(--color-accent-fill)" fillOpacity={0.8} />
                )}
              </svg>
              {typeMeta(type).label}
              <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
                {count}
              </span>
            </span>
          ))}
        {/* Say the bound. A view that quietly covers part of its subject reads
            as covering all of it. */}
        {dropped > 0 ? (
          <span className="ml-auto text-xs text-[var(--color-text-tertiary)]">
            {formatNumber(dropped)} not plotted — no size or dependency figures yet
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** The hover card, placed from the point's own coordinates. Flips to the other
 *  side near the right edge so it never leaves the plot. */
function MapCard({
  plan,
  x,
  y,
  width,
}: {
  plan: RefactoringPlan;
  x: number;
  y: number;
  width: number;
}) {
  const flip = x > width - 240;
  const meta = typeMeta(plan.refactoring_type);
  return (
    <div
      className="pointer-events-none absolute z-10 w-[220px] rounded-lg border border-[var(--color-border-hover)] bg-[var(--color-bg-surface)] px-3 py-2 shadow-md"
      style={{
        left: flip ? undefined : x + 16,
        right: flip ? width - x + 16 : undefined,
        top: Math.max(0, y - 62),
      }}
    >
      <div className="text-[11px] text-[var(--color-text-secondary)]">{meta.label}</div>
      <div className="mt-0.5 break-all font-mono text-xs font-semibold text-[var(--color-text-primary)]">
        {plan.file_path.split("/").pop()}
      </div>
      <div className="mt-1.5 border-t border-[var(--color-border-default)] pt-1.5 text-[11px] tabular-nums text-[var(--color-text-secondary)]">
        {formatNumber(plan.file_nloc ?? 0)} lines · {formatNumber(plan.dependents ?? 0)} dependent
        {(plan.dependents ?? 0) === 1 ? "" : "s"}
      </div>
    </div>
  );
}
