"use client";

import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { HEALTHY_MIN, NEEDS_WORK_MIN, formatScore } from "@repowise-dev/types/health";

import { EmptyState } from "../shared/empty-state";
import { seriesColor } from "../shared/series";
import { formatDate } from "../lib/format";
import { deltaColor, formatDelta } from "./tokens";

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

interface Series {
  key: LineKey;
  label: string;
  color: string;
  dash?: string;
}

/**
 * Identity, not verdict: health is the background bands' job, so a series at
 * 3 is not drawn in green. Each series keeps its colour whatever else is
 * shown. The three KPIs take the first three chart hues in order; the worst
 * file stays in ink and is dashed, so it is told apart without colour.
 */
const LINES: Record<LineKey, Series> = {
  maintainability_average: { key: "maintainability_average", label: "Maintainability", color: seriesColor(1) },
  average_health: { key: "average_health", label: "Code health", color: seriesColor(0) },
  hotspot_health: { key: "hotspot_health", label: "Hotspot", color: seriesColor(2) },
  worst_performer_score: {
    key: "worst_performer_score",
    label: "Worst file",
    color: "var(--color-text-tertiary)",
    dash: "4 3",
  },
};

/** The canonical three bands, top to bottom, as faint grounds behind the lines. */
const BANDS = [
  { label: "Healthy", from: 10, to: HEALTHY_MIN, fill: "var(--color-success)" },
  { label: "Warning", from: HEALTHY_MIN, to: NEEDS_WORK_MIN, fill: "var(--color-warning)" },
  { label: "Alert", from: NEEDS_WORK_MIN, to: 0, fill: "var(--color-error)" },
] as const;

// The right pad holds the band names, outside the lines they would collide with.
const PAD = { l: 28, r: 56, t: 10, b: 24 };
/** Until the container is measured (and in tests, which have no layout). */
const FALLBACK_WIDTH = 720;

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

function value(p: TrendSeriesPoint, key: LineKey): number | null {
  return p[key] ?? null;
}

/** Evenly spaced snapshot indices for the date axis, about one per 120px. */
function tickIndices(n: number, plotW: number): number[] {
  if (n <= 1) return [0];
  const count = Math.max(2, Math.min(n, Math.floor(plotW / 120) + 1));
  return [...new Set(Array.from({ length: count }, (_, k) => Math.round((k * (n - 1)) / (count - 1))))];
}

interface Geometry {
  width: number;
  height: number;
  plotW: number;
  x: (i: number) => number;
  y: (v: number) => number;
  paths: Record<LineKey, string>;
  historyBands: string[];
  ticks: { i: number; label: string }[];
}

/**
 * The dated ticks, one per label: several snapshots a day would otherwise
 * print the same date twice. The newest tick is the one kept.
 */
function dateTicks(history: TrendSeriesPoint[], indices: number[]): { i: number; label: string }[] {
  const out: { i: number; label: string }[] = [];
  for (const i of [...indices].reverse()) {
    const at = history[i]?.taken_at;
    if (!at) continue;
    const label = formatDate(at);
    if (out[0]?.label !== label) out.unshift({ i, label });
  }
  return out;
}

function geometry(history: TrendSeriesPoint[], width: number, height: number): Geometry {
  const plotW = Math.max(1, width - PAD.l - PAD.r);
  const plotH = Math.max(1, height - PAD.t - PAD.b);
  const n = history.length;
  const x = (i: number) => (n === 1 ? PAD.l + plotW / 2 : PAD.l + (i / (n - 1)) * plotW);
  const y = (v: number) => PAD.t + ((10 - v) / 10) * plotH;
  const path = (key: LineKey) =>
    runs(history, (p) => value(p, key) != null)
      .map((run) => run.map((i, j) => `${j === 0 ? "M" : "L"}${x(i)},${y(value(history[i]!, key)!)}`).join(" "))
      .join(" ");
  // The band between the score history does not touch and the score itself:
  // what git history costs, drawn where the readout says it in words.
  const historyBands = runs(history, (p) => p.history_average != null).map((run) =>
    [
      ...run.map((i, j) => {
        const p = history[i]!;
        // Clamped: the two halves are means of deductions while the score is
        // a mean of clamped scores, so on a repo with a floored file they can
        // sum past 10 and the edge would leave the plot.
        return `${j === 0 ? "M" : "L"}${x(i)},${y(Math.min(10, p.average_health + p.history_average!))}`;
      }),
      ...[...run].reverse().map((i) => `L${x(i)},${y(history[i]!.average_health)}`),
      "Z",
    ].join(" "),
  );
  return {
    width,
    height,
    plotW,
    x,
    y,
    paths: {
      average_health: path("average_health"),
      hotspot_health: path("hotspot_health"),
      worst_performer_score: path("worst_performer_score"),
      maintainability_average: path("maintainability_average"),
    },
    historyBands,
    ticks: dateTicks(history, tickIndices(n, plotW)),
  };
}

/** Bands, axes and lines: everything that does not move with the pointer. */
const Plot = memo(function Plot({
  g,
  history,
  series,
  lead,
  emphasis,
}: {
  g: Geometry;
  history: TrendSeriesPoint[];
  series: Series[];
  lead: LineKey;
  emphasis: LineKey | null;
}) {
  const showsCodeHealth = series.some((s) => s.key === "average_health");
  const opacity = (key: LineKey) => (emphasis && emphasis !== key ? 0.2 : 1);
  return (
    <>
      {BANDS.map((b) => (
        <g key={b.label}>
          <rect
            x={PAD.l}
            width={g.plotW}
            y={g.y(b.from)}
            height={g.y(b.to) - g.y(b.from)}
            fill={b.fill}
            fillOpacity={0.06}
          />
          <text
            x={PAD.l + g.plotW + 6}
            y={(g.y(b.from) + g.y(b.to)) / 2 + 3}
            fontSize={10}
            textAnchor="start"
            fill="var(--color-text-tertiary)"
          >
            {b.label}
          </text>
        </g>
      ))}
      {[0, 2, 4, 6, 8, 10].map((v) => (
        <g key={v}>
          <line x1={PAD.l} x2={PAD.l + g.plotW} y1={g.y(v)} y2={g.y(v)} stroke="currentColor" strokeOpacity={0.08} />
          <text x={PAD.l - 6} y={g.y(v) + 3} fontSize={10} textAnchor="end" fill="var(--color-text-tertiary)">
            {v}
          </text>
        </g>
      ))}
      {g.ticks.map(({ i, label }, k) => {
        const anchor = k === 0 ? "start" : k === g.ticks.length - 1 ? "end" : "middle";
        return (
          <text key={i} x={g.x(i)} y={g.height - 6} fontSize={10} textAnchor={anchor} fill="var(--color-text-tertiary)">
            {label}
          </text>
        );
      })}
      {/* Under the lines: the band is context for the code-health line, so its
          upper edge takes that line's colour and dims with it. */}
      {showsCodeHealth &&
        g.historyBands.map((d, i) => (
          <path
            key={i}
            d={d}
            fill="currentColor"
            fillOpacity={0.07}
            stroke={LINES.average_health.color}
            strokeOpacity={0.4 * opacity("average_health")}
            strokeWidth={1}
            strokeDasharray="2 3"
          />
        ))}
      {/* The lead last, so a support line never crosses it out. */}
      {[...series].sort((a, b) => Number(a.key === lead) - Number(b.key === lead)).map((s) => (
        <g key={s.key} opacity={opacity(s.key)}>
          <path
            d={g.paths[s.key]}
            stroke={s.color}
            strokeWidth={s.key === lead ? 2.5 : 1.5}
            strokeDasharray={s.dash}
            fill="none"
            strokeLinejoin="round"
          />
          {history.map((p, i) => {
            const v = value(p, s.key);
            return v == null ? null : <circle key={i} cx={g.x(i)} cy={g.y(v)} r={2} fill={s.color} />;
          })}
        </g>
      ))}
    </>
  );
});

/** One line of the readout per shown series, in the order the legend lists them. */
function readoutRows(history: TrendSeriesPoint[], i: number, series: Series[]) {
  const p = history[i]!;
  const prev = i > 0 ? history[i - 1]! : null;
  return series.flatMap((s) => {
    const v = value(p, s.key);
    if (v == null) return [];
    const before = prev ? value(prev, s.key) : null;
    return [{ series: s, value: v, delta: before == null ? null : v - before }];
  });
}

function historyWords(p: TrendSeriesPoint): string | null {
  if (p.history_average == null) return null;
  return `Change history takes ${p.history_average.toFixed(2)} points off code health.`;
}

/** The readout as one sentence, for the live region keyboard steps update. */
function announce(history: TrendSeriesPoint[], i: number, series: Series[]): string {
  const p = history[i]!;
  const parts = readoutRows(history, i, series).map(
    (r) => `${r.series.label} ${formatScore(r.value)}${r.delta == null ? "" : `, ${formatDelta(r.delta)}`}`,
  );
  const when = p.taken_at ? formatDate(p.taken_at) : "Undated snapshot";
  return [`${when}, snapshot ${i + 1} of ${history.length}.`, `${parts.join("; ")}.`, historyWords(p)]
    .filter(Boolean)
    .join(" ");
}

export function TrendChart({ history, height = 220 }: TrendChartProps) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  const [active, setActive] = useState<number | null>(null);
  const [spoken, setSpoken] = useState("");
  const [hidden, setHidden] = useState<ReadonlySet<LineKey>>(() => new Set());
  const [emphasis, setEmphasis] = useState<LineKey | null>(null);
  const frame = useRef<number | null>(null);
  const pointerX = useRef(0);

  // Sized to the container rather than scaled, so 10px labels stay 10px.
  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0);
      if (w > 0) setWidth(w);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(
    () => () => {
      if (frame.current != null) cancelAnimationFrame(frame.current);
    },
    [],
  );

  const g = useMemo(() => geometry(history, width, height), [history, width, height]);
  const available = useMemo(() => {
    const has = (key: LineKey) => history.some((p) => value(p, key) != null);
    return (["maintainability_average", "average_health", "hotspot_health", "worst_performer_score"] as const)
      .filter((key) => key === "average_health" || has(key))
      .map((key) => LINES[key]);
  }, [history]);
  // A new history can drop the series left on, so an empty pick falls back to
  // every series rather than drawing a blank plot.
  const shown = useMemo(() => {
    const picked = available.filter((s) => !hidden.has(s.key));
    return picked.length > 0 ? picked : available;
  }, [available, hidden]);

  // A tap shows the readout, so a touch leaving the plot does not clear it; a
  // press anywhere else does.
  useEffect(() => {
    if (active == null) return;
    const away = (e: globalThis.PointerEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setActive(null);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [active]);

  if (!history || history.length === 0) {
    return (
      <EmptyState
        size="compact"
        title="No snapshots yet"
        description="Each index update records one, and the trend appears from the second."
      />
    );
  }

  // Maintainability leads wherever it was recorded. It is the series that
  // answers "is my code getting better", and the composite beside it moves
  // with git history too, so drawing the composite loudest emphasises the
  // number that misleads.
  const lead: LineKey = available.some((s) => s.key === "maintainability_average")
    ? "maintainability_average"
    : "average_health";
  const last = history.length - 1;
  // A shorter history can leave the held index past its end.
  const at = active != null && active <= last ? active : null;

  const nearest = (clientX: number, el: Element) => {
    const left = el.getBoundingClientRect().left;
    const t = last === 0 ? 0 : (clientX - left - PAD.l) / g.plotW;
    return Math.max(0, Math.min(last, Math.round(t * last)));
  };
  // One state update per frame at most, and none when the snapshot is the same.
  const onPointerMove = (e: PointerEvent<SVGSVGElement>) => {
    pointerX.current = e.clientX;
    if (frame.current != null) return;
    const el = e.currentTarget;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const i = nearest(pointerX.current, el);
      setActive((prev) => (prev === i ? prev : i));
    });
  };
  const onPointerLeave = (e: PointerEvent<SVGSVGElement>) => {
    if (e.pointerType === "touch") return;
    if (frame.current != null) cancelAnimationFrame(frame.current);
    frame.current = null;
    setActive(null);
  };
  const step = (i: number) => {
    setActive(i);
    setSpoken(announce(history, i, shown));
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const from = at ?? last;
    const to =
      e.key === "ArrowLeft"
        ? Math.max(0, from - 1)
        : e.key === "ArrowRight"
          ? Math.min(last, from + 1)
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? last
              : null;
    if (to == null) {
      if (e.key === "Escape") setActive(null);
      return;
    }
    e.preventDefault();
    step(to);
  };

  // Flipped from what is drawn, not from the stored set, so a fallback to
  // every series cannot turn a click on one into hiding all the others.
  const toggle = (key: LineKey) => {
    const next = new Set(available.filter((s) => !shown.includes(s)).map((s) => s.key));
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setHidden(next);
  };

  const point = at == null ? null : history[at]!;
  const rows = at == null ? [] : readoutRows(history, at, shown);
  const activeX = at == null ? 0 : g.x(at);
  const readoutW = Math.min(250, g.width - 8);
  const readoutLeft = Math.max(
    4,
    Math.min(g.width - readoutW - 4, activeX > g.width / 2 ? activeX - 12 - readoutW : activeX + 12),
  );

  return (
    // No card. The chart sits inside a section that already names it, so a
    // border here is a second frame around content that has one.
    <div className="flex flex-col">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          KPI trend
        </h3>
        <div className="flex flex-wrap items-center gap-1 text-xs" role="group" aria-label="Series">
          {available.map((s) => {
            const on = shown.includes(s);
            // The last series shown cannot be hidden: an empty plot answers nothing.
            const locked = on && shown.length === 1;
            return (
              <button
                key={s.key}
                type="button"
                aria-pressed={on}
                disabled={locked}
                title={locked ? "At least one series stays on the chart" : undefined}
                onClick={() => toggle(s.key)}
                onPointerEnter={() => on && setEmphasis(s.key)}
                onPointerLeave={() => setEmphasis(null)}
                onFocus={() => on && setEmphasis(s.key)}
                onBlur={() => setEmphasis(null)}
                className={`inline-flex items-center gap-1.5 rounded px-1.5 py-1 transition-colors hover:bg-[var(--color-bg-elevated)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-primary)] disabled:cursor-default ${
                  on ? "text-[var(--color-text-secondary)]" : "text-[var(--color-text-tertiary)] line-through"
                }`}
              >
                <svg width="14" height="6" aria-hidden className={on ? "" : "opacity-40"}>
                  <line x1="0" x2="14" y1="3" y2="3" stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} />
                </svg>
                {s.label}
              </button>
            );
          })}
          {g.historyBands.length > 0 && !hidden.has("average_health") && (
            <span className="inline-flex items-center gap-1.5 px-1.5 text-[var(--color-text-tertiary)]">
              <span className="inline-block h-2 w-3 rounded-[1px] bg-current opacity-25" aria-hidden />
              History drag
            </span>
          )}
        </div>
      </div>
      <div
        ref={boxRef}
        tabIndex={0}
        role="group"
        aria-label="Health KPI trend. Arrow keys step through snapshots."
        onKeyDown={onKeyDown}
        onFocus={() => active == null && step(last)}
        onBlur={() => setActive(null)}
        className="relative rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent-primary)]"
      >
        <svg
          width={g.width}
          height={g.height}
          viewBox={`0 0 ${g.width} ${g.height}`}
          className="block max-w-full touch-pan-y"
          aria-hidden
          onPointerMove={onPointerMove}
          onPointerDown={onPointerMove}
          onPointerLeave={onPointerLeave}
        >
          <Plot g={g} history={history} series={shown} lead={lead} emphasis={emphasis} />
          {point && (
            <g pointerEvents="none">
              <line
                x1={activeX}
                x2={activeX}
                y1={PAD.t}
                y2={g.height - PAD.b}
                stroke="var(--color-text-tertiary)"
                strokeWidth={1}
              />
              {rows.map((r) => (
                <circle
                  key={r.series.key}
                  cx={activeX}
                  cy={g.y(r.value)}
                  r={4}
                  fill={r.series.color}
                  stroke="var(--color-bg-root)"
                  strokeWidth={2}
                />
              ))}
            </g>
          )}
        </svg>
        {point && (
          <div
            className="pointer-events-none absolute top-1 z-10 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-2.5 py-2 text-xs shadow-md"
            // Beside the crosshair on the roomier side, clamped inside the plot
            // so a narrow screen never pushes it past an edge.
            style={{ width: readoutW, left: readoutLeft }}
          >
            <p className="mb-1 flex items-baseline justify-between gap-3 text-[var(--color-text-primary)]">
              <span>{point.taken_at ? formatDate(point.taken_at) : "Undated snapshot"}</span>
              <span className="font-mono text-[10px] text-[var(--color-text-tertiary)]">
                {at! + 1} of {history.length}
              </span>
            </p>
            <dl className="grid grid-cols-[auto_auto_auto] items-center gap-x-3 gap-y-0.5">
              {rows.map((r) => (
                <div key={r.series.key} className="contents">
                  <dt className="inline-flex items-center gap-1.5 text-[var(--color-text-secondary)]">
                    <svg width="10" height="6" aria-hidden>
                      <line x1="0" x2="10" y1="3" y2="3" stroke={r.series.color} strokeWidth={2} strokeDasharray={r.series.dash} />
                    </svg>
                    {r.series.label}
                  </dt>
                  <dd className="text-right font-mono tabular-nums text-[var(--color-text-primary)]">
                    {formatScore(r.value)}
                  </dd>
                  <dd className={`text-right font-mono tabular-nums ${deltaColor(r.delta == null ? null : Number(r.delta.toFixed(2)))}`}>
                    {r.delta == null ? "" : formatDelta(r.delta)}
                  </dd>
                </div>
              ))}
            </dl>
            {historyWords(point) && (
              <p className="mt-1 text-[var(--color-text-tertiary)]">{historyWords(point)}</p>
            )}
          </div>
        )}
        <p className="sr-only" aria-live="polite">
          {spoken}
        </p>
      </div>
    </div>
  );
}
