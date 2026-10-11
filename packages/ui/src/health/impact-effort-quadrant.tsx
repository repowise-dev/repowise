"use client";

import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import type { ImpactEffortPoint, ImpactEffortResponse } from "@repowise-dev/types/health";

/**
 * Impact against effort for every file the queue's filters keep.
 *
 * The data is the whole filtered set from one server call, so the figure and
 * the queue's header count the same files. Both axes are continuous: x is the
 * lines a fix touches on a log scale, y the health points it recovers on a
 * square-root scale (most files recover under two points and a few recover
 * ten; a linear axis flattens the many into one line). The midlines come from
 * core constants in the response, never from the data, so a filter does not
 * move a file between quadrants.
 *
 * One mark per file and no colour ramp. A filled dot is placed by a refactoring
 * plan, a ring by the file itself (see `effort_basis`). Files holding a
 * Fix-first item are drawn as numbered ink markers carrying their Fix-first
 * rank, over a lighter field, so the plot points at what to do first. Orange
 * marks only the point under the pointer, the keyboard, or the reader's
 * selection. Legend, quadrant names and the coverage line sit outside the SVG.
 *
 * The dot field is memoised away from the hover state: a large repository
 * brings thousands of points, and hover must redraw one overlay mark, not the
 * field. Keyboard readers step through the points with the arrow keys (largest
 * gain first), which reads the same object a hover does.
 */

export type ImpactEffortQuadrantKey = "quick_wins" | "major_projects" | "minor_cleanups" | "time_sinks";

/** Reading order matches the plane's layout: top row, then bottom row. */
export const IMPACT_EFFORT_QUADRANTS: { key: ImpactEffortQuadrantKey; name: string }[] = [
  { key: "quick_wins", name: "Quick wins" },
  { key: "major_projects", name: "Major projects" },
  { key: "minor_cleanups", name: "Minor cleanups" },
  { key: "time_sinks", name: "Time sinks" },
];

export function impactEffortQuadrantOf(
  p: Pick<ImpactEffortPoint, "effort_lines" | "recoverable_health">,
  effortMid: number,
  gainMid: number,
): ImpactEffortQuadrantKey {
  const small = p.effort_lines < effortMid;
  const worth = p.recoverable_health >= gainMid;
  if (worth) return small ? "quick_wins" : "major_projects";
  return small ? "minor_cleanups" : "time_sinks";
}

const TIER_LABEL: Record<string, string> = { now: "Now", next: "Next", later: "Later" };

export interface ImpactEffortQuadrantProps {
  data: ImpactEffortResponse;
  /** The file the reader picked, drawn in the accent colour. */
  selectedPath?: string | null | undefined;
  /** Click or Enter on a point. */
  onSelect?: ((filePath: string) => void) | undefined;
  height?: number;
}

const PAD = { l: 40, r: 16, t: 20, b: 30 };
/** Clear space inside the axes, so no mark sits on the tick labels. */
const INSET = { l: 14, b: 8 };

/** A round value at or above *v*, so the top tick reads cleanly. */
function niceCeil(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

/** Candidate y ticks, kept clear of the midline label and of each other. */
const Y_TICKS = [0, 1, 2, 5, 10, 20, 50, 100];

function fmt(n: number): string {
  return n.toLocaleString();
}

function describe(p: ImpactEffortPoint): string {
  const gain = p.recoverable_health.toFixed(2);
  return p.effort_basis === "plan"
    ? `Plan changes ${fmt(p.effort_lines)} lines and recovers ${gain} points`
    : `No plan recovers health here: ${fmt(p.effort_lines)} code lines, findings deduct ${gain} points`;
}

export function ImpactEffortQuadrant({
  data,
  selectedPath,
  onSelect,
  height = 300,
}: ImpactEffortQuadrantProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Seeded so the first paint is a plausible chart, not a reflow a frame later.
  const [width, setWidth] = useState(900);
  const [active, setActive] = useState<number | null>(null);

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

  const points = data.points;
  const effortMid = data.effort_midline_lines;
  const gainMid = data.gain_midline_points;

  // A new dataset invalidates the index the pointer or keyboard was on.
  useEffect(() => setActive(null), [points]);

  const geom = useMemo(() => {
    const W = Math.max(280, Math.round(width));
    const H = W < 480 ? Math.min(height, 240) : height;
    const plotW = W - PAD.l - PAD.r;
    const plotH = H - PAD.t - PAD.b;
    let maxEffort = effortMid * 2;
    let maxGain = gainMid * 2;
    for (const p of points) {
      if (p.effort_lines > maxEffort) maxEffort = p.effort_lines;
      if (p.recoverable_health > maxGain) maxGain = p.recoverable_health;
    }
    const decades = Math.max(1, Math.ceil(Math.log10(maxEffort)));
    const yMax = niceCeil(maxGain);
    const x = (lines: number) =>
      PAD.l + INSET.l + (Math.log10(Math.max(1, lines)) / decades) * (plotW - INSET.l);
    const y = (gain: number) =>
      PAD.t + (1 - Math.sqrt(Math.max(0, gain) / yMax)) * (plotH - INSET.b);
    const xs = new Float64Array(points.length);
    const ys = new Float64Array(points.length);
    points.forEach((p, i) => {
      xs[i] = x(p.effort_lines);
      ys[i] = y(p.recoverable_health);
    });
    const xTicks = Array.from({ length: decades + 1 }, (_, i) => 10 ** i);
    const yTicks: number[] = [];
    for (const t of [...Y_TICKS.filter((v) => v < yMax), yMax]) {
      const clear = [gainMid, ...yTicks].every((u) => Math.abs(y(t) - y(u)) >= 12);
      if (clear) yTicks.push(t);
    }
    return { W, H, plotW, plotH, x, y, xs, ys, xTicks, yTicks };
  }, [width, height, points, effortMid, gainMid]);

  const counts = useMemo(() => {
    const out: Record<ImpactEffortQuadrantKey, number> = {
      quick_wins: 0,
      major_projects: 0,
      minor_cleanups: 0,
      time_sinks: 0,
    };
    for (const p of points) out[impactEffortQuadrantOf(p, effortMid, gainMid)] += 1;
    return out;
  }, [points, effortMid, gainMid]);

  const select = useCallback(
    (i: number) => {
      const p = points[i];
      if (p && onSelect) onSelect(p.file_path);
    },
    [points, onSelect],
  );

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (points.length === 0) return;
    const last = points.length - 1;
    let next: number | null = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = active == null ? 0 : Math.min(last, active + 1);
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = active == null ? 0 : Math.max(0, active - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    else if ((e.key === "Enter" || e.key === " ") && active != null) {
      e.preventDefault();
      select(active);
      return;
    }
    if (next == null) return;
    e.preventDefault();
    setActive(next);
  };

  const selectedIndex = useMemo(
    () => (selectedPath ? points.findIndex((p) => p.file_path === selectedPath) : -1),
    [points, selectedPath],
  );
  const shown = active != null ? points[active] : selectedIndex >= 0 ? points[selectedIndex] : null;
  const { W, H, plotW, plotH, x, y, xs, ys, xTicks, yTicks } = geom;
  const midX = x(effortMid);
  const midY = y(gainMid);
  const capped = data.plotted < data.total;
  const historyHidden = data.history_only_excluded ?? 0;

  return (
    <section className="space-y-3" aria-labelledby="impact-effort-title">
      <div className="space-y-1">
        <h3
          id="impact-effort-title"
          className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]"
        >
          Impact and effort
        </h3>
        <p className="text-xs text-[var(--color-text-secondary)]">
          <span className="font-mono tabular-nums">{fmt(data.plotted)}</span> plotted of{" "}
          <span className="font-mono tabular-nums">{fmt(data.total)}</span>{" "}
          {data.total === 1 ? "file" : "files"} in this filter
          {capped ? `, capped at ${fmt(data.cap)}: the largest gains are kept` : ""}. Lines
          on a log scale, health points on a square-root scale.
          {historyHidden > 0
            ? ` ${fmt(historyHidden)} ${historyHidden === 1 ? "file" : "files"} with only history signals ${historyHidden === 1 ? "is" : "are"} not plotted: no edit recovers that health.`
            : ""}
        </p>
      </div>

      <div ref={containerRef} className="min-w-0">
        <div
          tabIndex={0}
          role="group"
          aria-label={`Impact and effort plot of ${fmt(data.plotted)} files. Arrow keys step through them, largest gain first. Enter finds the file in the list.`}
          onKeyDown={onKeyDown}
          onBlur={() => setActive(null)}
          className="rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-primary)]"
        >
          <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true" className="block">
            {/* Axes */}
            <line x1={PAD.l} y1={PAD.t + plotH} x2={PAD.l + plotW} y2={PAD.t + plotH} className="stroke-[var(--color-border-default)]" />
            <line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={PAD.t + plotH} className="stroke-[var(--color-border-default)]" />
            {xTicks.map((t, i) => (
              <text key={`x${t}`} x={x(t)} y={PAD.t + plotH + 14} textAnchor={i === xTicks.length - 1 ? "end" : "middle"} fontSize={10} className="fill-[var(--color-text-tertiary)] font-mono">
                {fmt(t)}
              </text>
            ))}
            {yTicks.map((t) => (
              <text key={`y${t}`} x={PAD.l - 6} y={y(t) + 3} textAnchor="end" fontSize={10} className="fill-[var(--color-text-tertiary)] font-mono">
                {t}
              </text>
            ))}
            <text x={PAD.l + plotW / 2} y={H - 4} textAnchor="middle" fontSize={10} className="fill-[var(--color-text-secondary)]">
              Lines to change
            </text>
            <text x={10} y={PAD.t + plotH / 2} textAnchor="middle" fontSize={10} transform={`rotate(-90 10 ${PAD.t + plotH / 2})`} className="fill-[var(--color-text-secondary)]">
              Recoverable health
            </text>
            {/* Fixed midlines from core */}
            <line x1={midX} y1={PAD.t} x2={midX} y2={PAD.t + plotH} strokeDasharray="3 3" className="stroke-[var(--color-border-hover)]" />
            <line x1={PAD.l} y1={midY} x2={PAD.l + plotW} y2={midY} strokeDasharray="3 3" className="stroke-[var(--color-border-hover)]" />
            <text x={midX} y={PAD.t - 6} textAnchor="middle" fontSize={10} className="fill-[var(--color-text-secondary)] font-mono">
              {fmt(effortMid)} lines
            </text>
            <text x={PAD.l - 6} y={midY + 3} textAnchor="end" fontSize={10} className="fill-[var(--color-text-secondary)] font-mono">
              {gainMid}
            </text>

            <PointField points={points} xs={xs} ys={ys} onHover={setActive} onPick={select} />

            {selectedIndex >= 0 ? (
              points[selectedIndex]?.fix_rank != null ? (
                // A ring, so the rank number under it stays readable.
                <circle cx={xs[selectedIndex]} cy={ys[selectedIndex]} r={8.5} className="fill-none stroke-[var(--color-accent-primary)]" strokeWidth={2.5} pointerEvents="none" />
              ) : (
                <circle cx={xs[selectedIndex]} cy={ys[selectedIndex]} r={5.5} className="fill-[var(--color-accent-primary)] stroke-[var(--color-bg-root)]" strokeWidth={1.5} pointerEvents="none" />
              )
            ) : null}
            {active != null && active !== selectedIndex ? (
              <circle cx={xs[active]} cy={ys[active]} r={points[active]?.fix_rank != null ? 9 : 5.5} className="fill-none stroke-[var(--color-accent-primary)]" strokeWidth={2} pointerEvents="none" />
            ) : null}
          </svg>
        </div>
        <p aria-live="polite" className="mt-1 min-h-[2.5rem] text-xs text-[var(--color-text-secondary)]">
          {shown ? (
            <>
              <span className="font-mono text-[var(--color-text-primary)] break-all">{shown.file_path}</span>
              <br />
              {describe(shown)}
              {shown.fix_rank != null
                ? ` · Fix first #${shown.fix_rank}${shown.tier ? `, ${TIER_LABEL[shown.tier] ?? shown.tier}` : ""}`
                : ""}
              {" · "}
              {IMPACT_EFFORT_QUADRANTS.find((q) => q.key === impactEffortQuadrantOf(shown, effortMid, gainMid))?.name}
            </>
          ) : (
            <span className="text-[var(--color-text-tertiary)]">
              Point at a file, or focus the plot and use the arrow keys. Click or press Enter to find it in the list.
            </span>
          )}
        </p>
      </div>

      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-2" aria-label="Quadrants">
        {IMPACT_EFFORT_QUADRANTS.map((q) => (
          <div key={q.key} className="min-w-0">
            <dt className="inline font-medium text-[var(--color-text-primary)]">{q.name}</dt>{" "}
            <dd className="inline text-[var(--color-text-secondary)]">
              <span className="font-mono tabular-nums">{fmt(counts[q.key])}</span>
              {" · "}
              {q.key === "quick_wins" || q.key === "minor_cleanups" ? "under" : "at least"} {fmt(effortMid)} lines,{" "}
              {q.key === "quick_wins" || q.key === "major_projects" ? "at least" : "under"} {gainMid} points
            </dd>
          </div>
        ))}
      </dl>

      <ul className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--color-text-secondary)]" aria-label="Legend">
        <li className="inline-flex items-center gap-1.5">
          <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3.5" className="fill-[var(--color-text-tertiary)]" /></svg>
          Placed by its refactoring plan: lines it changes, health it recovers
        </li>
        <li className="inline-flex items-center gap-1.5">
          <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3" className="fill-none stroke-[var(--color-text-tertiary)]" strokeWidth={1.25} /></svg>
          No plan recovers health: code lines, and what its findings deduct
        </li>
        {points.some((p) => p.fix_rank != null) ? (
          <li className="inline-flex items-center gap-1.5">
            <svg width="16" height="16" aria-hidden="true">
              <circle cx="8" cy="8" r="7" className="fill-[var(--color-bg-root)] stroke-[var(--color-text-primary)]" strokeWidth={1.25} />
              <text x="8" y="11" textAnchor="middle" fontSize={9} fontWeight={600} className="fill-[var(--color-text-primary)] font-mono">1</text>
            </svg>
            A Fix-first item, numbered by its place in Fix first
          </li>
        ) : null}
        <li className="inline-flex items-center gap-1.5">
          <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" className="fill-[var(--color-accent-primary)]" /></svg>
          Selected
        </li>
      </ul>
    </section>
  );
}

interface PointFieldProps {
  points: ImpactEffortPoint[];
  xs: Float64Array;
  ys: Float64Array;
  onHover: (i: number | null) => void;
  onPick: (i: number) => void;
}

function indexOf(e: PointerEvent<SVGGElement>): number | null {
  const raw = (e.target as Element).getAttribute?.("data-i");
  return raw == null ? null : Number(raw);
}

/**
 * Every point, drawn once per dataset. Handlers are delegated to the group so
 * no point carries its own closure, and hover state lives in the parent's
 * overlay marks, never here.
 */
const PointField = memo(function PointField({ points, xs, ys, onHover, onPick }: PointFieldProps) {
  return (
    <g
      data-testid="impact-effort-points"
      className="cursor-pointer"
      onPointerOver={(e) => {
        const i = indexOf(e);
        if (i != null) onHover(i);
      }}
      onPointerLeave={() => onHover(null)}
      onClick={(e) => {
        const i = indexOf(e as unknown as PointerEvent<SVGGElement>);
        if (i != null) onPick(i);
      }}
    >
      {points.map((p, i) =>
        p.fix_rank != null ? null : p.effort_basis === "plan" ? (
          <circle key={p.file_path} data-i={i} data-file={p.file_path} cx={xs[i]} cy={ys[i]} r={3} className="fill-[var(--color-text-tertiary)]" fillOpacity={0.35} />
        ) : (
          <circle key={p.file_path} data-i={i} data-file={p.file_path} cx={xs[i]} cy={ys[i]} r={2.75} className="fill-transparent stroke-[var(--color-text-tertiary)]" strokeOpacity={0.45} strokeWidth={1.25} />
        ),
      )}
      {/* Fix-first items last, so they sit on top of the field. Ink weight,
          not colour: orange stays for selection. */}
      {points.map((p, i) =>
        p.fix_rank == null ? null : (
          <g key={p.file_path} data-rank={p.fix_rank}>
            <circle data-i={i} data-file={p.file_path} cx={xs[i]} cy={ys[i]} r={7} className="fill-[var(--color-bg-root)] stroke-[var(--color-text-primary)]" strokeWidth={1.25} />
            <text x={xs[i]} y={(ys[i] ?? 0) + 3} textAnchor="middle" fontSize={9} fontWeight={600} pointerEvents="none" className="fill-[var(--color-text-primary)] font-mono">
              {p.fix_rank}
            </text>
          </g>
        ),
      )}
    </g>
  );
});
