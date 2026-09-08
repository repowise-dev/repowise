"use client";

import { formatDate } from "../lib/format";

export interface TrendSeriesPoint {
  taken_at: string | null;
  /** `null` under a narrowed scope, which records only the average. */
  hotspot_health: number | null;
  average_health: number;
  worst_performer_score: number | null;
  /**
   * The maintainability pillar, and the deduction history costs, at this
   * snapshot. Both `null` before they were recorded, so each series starts
   * partway along the axis instead of reading an unrecorded point as a zero.
   */
  maintainability_average?: number | null;
  history_average?: number | null;
}

export interface TrendChartProps {
  /** Oldest-first list of snapshots. */
  history: TrendSeriesPoint[];
  height?: number;
}

type LineKey =
  | "average_health"
  | "hotspot_health"
  | "worst_performer_score"
  | "maintainability_average";

/**
 * Index ranges over which *has* holds without a gap.
 *
 * A line may simply skip a missing point, but an area cannot: filling straight
 * across a gap would draw a measurement for a snapshot that never recorded one.
 */
function runs<T>(items: T[], has: (item: T) => boolean): number[][] {
  const out: number[][] = [];
  let current: number[] = [];
  items.forEach((item, i) => {
    if (has(item)) {
      current.push(i);
      return;
    }
    if (current.length > 0) out.push(current);
    current = [];
  });
  if (current.length > 0) out.push(current);
  return out;
}

export function TrendChart({ history, height = 220 }: TrendChartProps) {
  if (!history || history.length === 0) {
    return (
      <p className="max-w-[62ch] text-sm text-[var(--color-text-secondary)]">
        No snapshots yet. Each index or sync records one; the trend appears from the
        second snapshot on.
      </p>
    );
  }

  const W = 720;
  const H = height;
  const padL = 36;
  const padR = 12;
  const padT = 12;
  const padB = 26;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const xScale = (i: number) =>
    history.length === 1 ? padL + plotW / 2 : padL + (i / (history.length - 1)) * plotW;
  const yScale = (v: number) => padT + ((10 - v) / 10) * plotH;

  const path = (key: LineKey) => {
    const pts: [number, number][] = [];
    history.forEach((p, i) => {
      const v = p[key];
      if (v == null) return;
      pts.push([xScale(i), yScale(v as number)]);
    });
    return pts.map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`)).join(" ");
  };

  const hasMaintainability = history.some((p) => p.maintainability_average != null);
  // Maintainability leads wherever it was recorded. It is the series that
  // answers "is my code getting better", and the composite it sits beside
  // moves with git history too, so drawing the composite loudest emphasises
  // the number that misleads. Without a maintainability series the composite
  // is the only headline there is, and keeps the weight.
  const leadWidth = 2.4;
  const supportWidth = hasMaintainability ? 1.4 : 1.8;

  // The band between the score history does not touch and the score itself:
  // what git history costs, drawn where the lede says it in words. Only over
  // runs of snapshots that recorded the split.
  const historyBands = runs(history, (p) => p.history_average != null).map((run) =>
    [
      ...run.map((i) => {
        const p = history[i]!;
        // Clamped: the two halves are means of deductions while the score is
        // a mean of clamped scores, so on a repo with a floored file they can
        // sum past 10 and the edge would leave the plot.
        return `${run[0] === i ? "M" : "L"}${xScale(i)},${yScale(
          Math.min(10, p.average_health + (p.history_average as number)),
        )}`;
      }),
      ...[...run].reverse().map((i) => `L${xScale(i)},${yScale(history[i]!.average_health)}`),
      "Z",
    ].join(" "),
  );

  return (
    // No card. The chart sits inside a section that already names it, so a
    // border here is a second frame around content that has one.
    <div className="flex flex-col">
      <div className="mb-2 flex items-center justify-between gap-4">
        <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          KPI trend
        </h3>
        <div className="flex flex-wrap items-center gap-3 text-xs text-[var(--color-text-tertiary)]">
          {hasMaintainability && (
            <Legend dot="bg-[var(--color-accent-secondary)]" label="Maintainability" />
          )}
          <Legend dot="bg-[var(--color-success)]" label="Code health" />
          {historyBands.length > 0 && (
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-[1px] bg-current opacity-25" />
              History drag
            </span>
          )}
          <Legend dot="bg-[var(--color-warning)]" label="Hotspot" />
          <Legend dot="bg-[var(--color-error)]" label="Worst" />
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Health KPI trend">
        {/* Y grid */}
        {[0, 2, 4, 6, 8, 10].map((v) => (
          <g key={v}>
            <line x1={padL} x2={W - padR} y1={yScale(v)} y2={yScale(v)} stroke="currentColor" strokeOpacity={0.08} />
            <text x={padL - 6} y={yScale(v) + 3} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>
              {v}
            </text>
          </g>
        ))}
        {/* Under the lines: the band is context for them, not a mark of its own.
            Its edges are stroked in the code-health colour because the band
            belongs to that line — it runs from the score up to where the score
            would sit with no history against it. Unstroked, it read as bounding
            whichever line happened to fall inside it. The lower edge lies on the
            code-health line itself, so only the upper one shows. */}
        {historyBands.map((d, i) => (
          <path
            key={i}
            d={d}
            fill="currentColor"
            fillOpacity={0.07}
            stroke="var(--color-success)"
            strokeOpacity={0.35}
            strokeWidth={1}
            strokeDasharray="2 3"
          />
        ))}
        <path
          d={path("average_health")}
          stroke="var(--color-success)"
          strokeWidth={supportWidth}
          fill="none"
        />
        <path
          d={path("hotspot_health")}
          stroke="var(--color-warning)"
          strokeWidth={supportWidth}
          fill="none"
        />
        <path d={path("worst_performer_score")} stroke="var(--color-error)" strokeWidth={1.4} fill="none" strokeDasharray="3 3" />
        {/* Last, so the lead series is never crossed out by a support line. */}
        {hasMaintainability && (
          <path
            d={path("maintainability_average")}
            stroke="var(--color-accent-secondary)"
            strokeWidth={leadWidth}
            fill="none"
          />
        )}
        {history.map((p, i) => (
          <g key={i}>
            <circle
              cx={xScale(i)}
              cy={yScale(p.average_health)}
              r={hasMaintainability ? 2 : 2.5}
              fill="var(--color-success)"
            />
            {p.hotspot_health != null ? (
              <circle
                cx={xScale(i)}
                cy={yScale(p.hotspot_health)}
                r={hasMaintainability ? 2 : 2.5}
                fill="var(--color-warning)"
              />
            ) : null}
            {p.worst_performer_score != null ? (
              <circle cx={xScale(i)} cy={yScale(p.worst_performer_score)} r={2} fill="var(--color-error)" />
            ) : null}
            {p.maintainability_average != null ? (
              <circle
                cx={xScale(i)}
                cy={yScale(p.maintainability_average)}
                r={3}
                fill="var(--color-accent-secondary)"
              />
            ) : null}
          </g>
        ))}
        {history.length > 1 ? (
          <>
            <text x={padL} y={H - 8} fontSize={10} fill="currentColor" opacity={0.5}>
              {history[0]?.taken_at ? formatDate(history[0]!.taken_at!) : ""}
            </text>
            <text x={W - padR} y={H - 8} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>
              {history[history.length - 1]?.taken_at
                ? formatDate(history[history.length - 1]!.taken_at!)
                : ""}
            </text>
          </>
        ) : null}
      </svg>
    </div>
  );
}

function Legend({ dot, label }: { dot: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`inline-block h-2 w-2 rounded-full ${dot}`} />
      {label}
    </span>
  );
}
