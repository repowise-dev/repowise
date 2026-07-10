"use client";

export interface TrendSeriesPoint {
  taken_at: string | null;
  hotspot_health: number;
  average_health: number;
  worst_performer_score: number | null;
}

export interface TrendChartProps {
  /** Oldest-first list of snapshots. */
  history: TrendSeriesPoint[];
  height?: number;
}

export function TrendChart({ history, height = 220 }: TrendChartProps) {
  if (!history || history.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-center text-sm text-[var(--color-text-tertiary)]">
        No snapshots yet. Each index or sync records one; the trend appears
        from the second snapshot on.
      </div>
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

  const path = (key: "average_health" | "hotspot_health" | "worst_performer_score") => {
    const pts: [number, number][] = [];
    history.forEach((p, i) => {
      const v = p[key];
      if (v == null) return;
      pts.push([xScale(i), yScale(v as number)]);
    });
    return pts.map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`)).join(" ");
  };

  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
      <div className="flex items-center justify-between gap-4 mb-2">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
          KPI trend
        </h3>
        <div className="flex items-center gap-3 text-xs text-[var(--color-text-tertiary)]">
          <Legend dot="bg-[var(--color-success)]" label="Average" />
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
        <path d={path("average_health")} stroke="var(--color-success)" strokeWidth={1.8} fill="none" />
        <path d={path("hotspot_health")} stroke="var(--color-warning)" strokeWidth={1.8} fill="none" />
        <path d={path("worst_performer_score")} stroke="var(--color-error)" strokeWidth={1.4} fill="none" strokeDasharray="3 3" />
        {history.map((p, i) => (
          <g key={i}>
            <circle cx={xScale(i)} cy={yScale(p.average_health)} r={2.5} fill="var(--color-success)" />
            <circle cx={xScale(i)} cy={yScale(p.hotspot_health)} r={2.5} fill="var(--color-warning)" />
            {p.worst_performer_score != null ? (
              <circle cx={xScale(i)} cy={yScale(p.worst_performer_score)} r={2} fill="var(--color-error)" />
            ) : null}
          </g>
        ))}
        {history.length > 1 ? (
          <>
            <text x={padL} y={H - 8} fontSize={10} fill="currentColor" opacity={0.5}>
              {history[0]?.taken_at ? new Date(history[0]!.taken_at!).toLocaleDateString() : ""}
            </text>
            <text x={W - padR} y={H - 8} fontSize={10} textAnchor="end" fill="currentColor" opacity={0.5}>
              {history[history.length - 1]?.taken_at
                ? new Date(history[history.length - 1]!.taken_at!).toLocaleDateString()
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
