"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent, RefObject } from "react";
import { formatScore } from "@repowise-dev/types/health";

import { EmptyState } from "../shared/empty-state";
import { seriesColor } from "../shared/series";
import { formatDate } from "../lib/format";
import { HEALTH_MARK_BANDS, deltaColor, formatDelta } from "./tokens";

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
const BANDS = HEALTH_MARK_BANDS.map((b, i) => ({
  label: b.label,
  from: i === 0 ? 10 : HEALTH_MARK_BANDS[i - 1]!.min,
  to: b.min,
  fill: b.color,
}));

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

const MICRO = { fontSize: 10, fill: "var(--color-text-tertiary)" } as const;

/** The three health bands, named in the right margin. */
function Bands({ g }: { g: Geometry }) {
  return BANDS.map((b) => (
    <g key={b.label}>
      <rect x={PAD.l} width={g.plotW} y={g.y(b.from)} height={g.y(b.to) - g.y(b.from)} fill={b.fill} fillOpacity={0.06} />
      <text x={PAD.l + g.plotW + 6} y={(g.y(b.from) + g.y(b.to)) / 2 + 3} textAnchor="start" {...MICRO}>
        {b.label}
      </text>
    </g>
  ));
}

/** The score gridlines and the date ticks. */
function Axes({ g }: { g: Geometry }) {
  return (
    <>
      {[0, 2, 4, 6, 8, 10].map((v) => (
        <g key={v}>
          <line x1={PAD.l} x2={PAD.l + g.plotW} y1={g.y(v)} y2={g.y(v)} stroke="currentColor" strokeOpacity={0.08} />
          <text x={PAD.l - 6} y={g.y(v) + 3} textAnchor="end" {...MICRO}>
            {v}
          </text>
        </g>
      ))}
      {g.ticks.map(({ i, label }, k) => (
        <text
          key={i}
          x={g.x(i)}
          y={g.height - 6}
          textAnchor={k === 0 ? "start" : k === g.ticks.length - 1 ? "end" : "middle"}
          {...MICRO}
        >
          {label}
        </text>
      ))}
    </>
  );
}

/**
 * Under the lines: the band is context for the code-health line, so its upper
 * edge takes that line's colour and dims with it.
 */
function HistoryDrag({ g, opacity }: { g: Geometry; opacity: number }) {
  return g.historyBands.map((d, i) => (
    <path
      key={i}
      d={d}
      fill="currentColor"
      fillOpacity={0.07}
      stroke={LINES.average_health.color}
      strokeOpacity={0.4 * opacity}
      strokeWidth={1}
      strokeDasharray="2 3"
    />
  ));
}

function Line({ g, history, s, lead }: { g: Geometry; history: TrendSeriesPoint[]; s: Series; lead: boolean }) {
  return (
    <>
      <path
        d={g.paths[s.key]}
        stroke={s.color}
        strokeWidth={lead ? 2.5 : 1.5}
        strokeDasharray={s.dash}
        fill="none"
        strokeLinejoin="round"
      />
      {history.map((p, i) => {
        const v = value(p, s.key);
        return v == null ? null : <circle key={i} cx={g.x(i)} cy={g.y(v)} r={2} fill={s.color} />;
      })}
    </>
  );
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
  const opacity = (key: LineKey) => (emphasis && emphasis !== key ? 0.2 : 1);
  // The lead last, so a support line never crosses it out.
  const ordered = [...series].sort((a, b) => Number(a.key === lead) - Number(b.key === lead));
  return (
    <>
      <Bands g={g} />
      <Axes g={g} />
      {series.some((s) => s.key === "average_health") && (
        <HistoryDrag g={g} opacity={opacity("average_health")} />
      )}
      {ordered.map((s) => (
        <g key={s.key} opacity={opacity(s.key)}>
          <Line g={g} history={history} s={s} lead={s.key === lead} />
        </g>
      ))}
    </>
  );
});

type ReadoutRow = { series: Series; value: number; delta: number | null };

/** One line of the readout per shown series, in the order the legend lists them. */
function readoutRows(history: TrendSeriesPoint[], i: number, series: Series[]): ReadoutRow[] {
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

/** The snapshot a key moves to from *from*, or `null` for a key that steps nowhere. */
function keyStep(key: string, from: number, last: number): number | null {
  if (key === "ArrowLeft") return Math.max(0, from - 1);
  if (key === "ArrowRight") return Math.min(last, from + 1);
  if (key === "Home") return 0;
  if (key === "End") return last;
  return null;
}

/** The container's width, so the chart is sized rather than scaled and 10px labels stay 10px. */
function useChartSize(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0);
      if (w > 0) setWidth(w);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

/** Which series the history carries, which are drawn, and the legend's toggle. */
function useSeriesVisibility(history: TrendSeriesPoint[]) {
  const [hidden, setHidden] = useState<ReadonlySet<LineKey>>(() => new Set());
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
  // Flipped from what is drawn, not from the stored set, so a fallback to
  // every series cannot turn a click on one into hiding all the others.
  const toggle = (key: LineKey) => {
    const next = new Set(available.filter((s) => !shown.includes(s)).map((s) => s.key));
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setHidden(next);
  };
  // Maintainability leads wherever it was recorded. It is the series that
  // answers "is my code getting better", and the composite beside it moves
  // with git history too, so drawing the composite loudest emphasises the
  // number that misleads.
  const lead: LineKey = available.some((s) => s.key === "maintainability_average")
    ? "maintainability_average"
    : "average_health";
  return { available, shown, toggle, lead };
}

/**
 * Pointer handlers that move the active snapshot, at most once a frame. A tap
 * shows the readout, so a touch leaving the plot does not clear it.
 */
function usePointerSnapshot(
  setActive: (update: (prev: number | null) => number | null) => void,
  last: number,
  plotW: number,
) {
  const frame = useRef<number | null>(null);
  const pointerX = useRef(0);
  useEffect(
    () => () => {
      if (frame.current != null) cancelAnimationFrame(frame.current);
    },
    [],
  );
  const nearest = (clientX: number, el: Element) => {
    const t = last === 0 ? 0 : (clientX - el.getBoundingClientRect().left - PAD.l) / plotW;
    return Math.max(0, Math.min(last, Math.round(t * last)));
  };
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
    setActive(() => null);
  };
  return { onPointerMove, onPointerDown: onPointerMove, onPointerLeave };
}

/** Clears the active snapshot on a press outside *ref*, while one is held. */
function useClearOnPressAway(ref: RefObject<HTMLElement | null>, held: boolean, clear: () => void) {
  useEffect(() => {
    if (!held) return;
    const away = (e: globalThis.PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) clear();
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [held, ref, clear]);
}

/**
 * The snapshot under the pointer or the keyboard cursor, the handlers that
 * move it, and the sentence keyboard steps announce.
 */
function useActiveSnapshot(
  boxRef: RefObject<HTMLElement | null>,
  history: TrendSeriesPoint[],
  shown: Series[],
  plotW: number,
) {
  const [active, setActive] = useState<number | null>(null);
  const [spoken, setSpoken] = useState("");
  const last = history.length - 1;
  // A shorter history can leave the held index past its end.
  const at = active != null && active <= last ? active : null;
  const svg = usePointerSnapshot(setActive, last, plotW);
  const clear = useCallback(() => setActive(null), []);
  useClearOnPressAway(boxRef, active != null, clear);

  const step = (i: number) => {
    setActive(i);
    setSpoken(announce(history, i, shown));
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const to = keyStep(e.key, at ?? last, last);
    if (to != null) {
      e.preventDefault();
      step(to);
    } else if (e.key === "Escape") clear();
  };
  const box = { onKeyDown, onFocus: () => active == null && step(last), onBlur: clear };
  return { at, spoken, svg, box };
}

function Swatch({ s, width, dim = false }: { s: Series; width: number; dim?: boolean }) {
  return (
    <svg width={width} height="6" aria-hidden className={dim ? "opacity-40" : ""}>
      <line x1="0" x2={width} y1="3" y2="3" stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} />
    </svg>
  );
}

function LegendButton({
  s,
  on,
  locked,
  onToggle,
  onEmphasis,
}: {
  s: Series;
  on: boolean;
  locked: boolean;
  onToggle: (key: LineKey) => void;
  onEmphasis: (key: LineKey | null) => void;
}) {
  const emphasise = () => on && onEmphasis(s.key);
  return (
    <button
      type="button"
      aria-pressed={on}
      disabled={locked}
      title={locked ? "At least one series stays on the chart" : undefined}
      onClick={() => onToggle(s.key)}
      onPointerEnter={emphasise}
      onPointerLeave={() => onEmphasis(null)}
      onFocus={emphasise}
      onBlur={() => onEmphasis(null)}
      className={`inline-flex items-center gap-1.5 rounded px-1.5 py-1 transition-colors hover:bg-[var(--color-bg-elevated)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-focus-ring)] disabled:cursor-default ${
        on ? "text-[var(--color-text-secondary)]" : "text-[var(--color-text-tertiary)] line-through"
      }`}
    >
      <Swatch s={s} width={14} dim={!on} />
      {s.label}
    </button>
  );
}

function Legend({
  available,
  shown,
  drag,
  onToggle,
  onEmphasis,
}: {
  available: Series[];
  shown: Series[];
  /** Whether the history-drag band is drawn, so its key is listed. */
  drag: boolean;
  onToggle: (key: LineKey) => void;
  onEmphasis: (key: LineKey | null) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1 text-xs" role="group" aria-label="Series">
      {available.map((s) => {
        const on = shown.includes(s);
        // The last series shown cannot be hidden: an empty plot answers nothing.
        return (
          <LegendButton
            key={s.key}
            s={s}
            on={on}
            locked={on && shown.length === 1}
            onToggle={onToggle}
            onEmphasis={onEmphasis}
          />
        );
      })}
      {drag && (
        <span className="inline-flex items-center gap-1.5 px-1.5 text-[var(--color-text-tertiary)]">
          <span className="inline-block h-2 w-3 rounded-[1px] bg-current opacity-25" aria-hidden />
          History drag
        </span>
      )}
    </div>
  );
}

function Crosshair({ g, x, rows }: { g: Geometry; x: number; rows: ReadoutRow[] }) {
  return (
    <g pointerEvents="none">
      <line x1={x} x2={x} y1={PAD.t} y2={g.height - PAD.b} stroke="var(--color-text-tertiary)" strokeWidth={1} />
      {rows.map((r) => (
        <circle
          key={r.series.key}
          cx={x}
          cy={g.y(r.value)}
          r={4}
          fill={r.series.color}
          stroke="var(--color-bg-root)"
          strokeWidth={2}
        />
      ))}
    </g>
  );
}

function ReadoutLine({ r }: { r: ReadoutRow }) {
  const tone = deltaColor(r.delta == null ? null : Number(r.delta.toFixed(2)));
  return (
    <div className="contents">
      <dt className="inline-flex items-center gap-1.5 text-[var(--color-text-secondary)]">
        <Swatch s={r.series} width={10} />
        {r.series.label}
      </dt>
      <dd className="text-right font-mono tabular-nums text-[var(--color-text-primary)]">{formatScore(r.value)}</dd>
      <dd className={`text-right font-mono tabular-nums ${tone}`}>{r.delta == null ? "" : formatDelta(r.delta)}</dd>
    </div>
  );
}

function Readout({
  g,
  x,
  at,
  history,
  rows,
}: {
  g: Geometry;
  x: number;
  at: number;
  history: TrendSeriesPoint[];
  rows: ReadoutRow[];
}) {
  const point = history[at]!;
  const width = Math.min(250, g.width - 8);
  // Beside the crosshair on the roomier side, clamped inside the plot so a
  // narrow screen never pushes it past an edge.
  const left = Math.max(4, Math.min(g.width - width - 4, x > g.width / 2 ? x - 12 - width : x + 12));
  const words = historyWords(point);
  return (
    <div
      className="pointer-events-none absolute top-1 z-10 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-2.5 py-2 text-xs shadow-md"
      style={{ width, left }}
    >
      <p className="mb-1 flex items-baseline justify-between gap-3 text-[var(--color-text-primary)]">
        <span>{point.taken_at ? formatDate(point.taken_at) : "Undated snapshot"}</span>
        <span className="font-mono text-[10px] text-[var(--color-text-tertiary)]">
          {at + 1} of {history.length}
        </span>
      </p>
      <dl className="grid grid-cols-[auto_auto_auto] items-center gap-x-3 gap-y-0.5">
        {rows.map((r) => (
          <ReadoutLine key={r.series.key} r={r} />
        ))}
      </dl>
      {words && <p className="mt-1 text-[var(--color-text-tertiary)]">{words}</p>}
    </div>
  );
}

export function TrendChart({ history, height = 220 }: TrendChartProps) {
  const boxRef = useRef<HTMLDivElement>(null);
  const width = useChartSize(boxRef);
  const [emphasis, setEmphasis] = useState<LineKey | null>(null);
  const g = useMemo(() => geometry(history, width, height), [history, width, height]);
  const { available, shown, toggle, lead } = useSeriesVisibility(history);
  const { at, spoken, svg, box } = useActiveSnapshot(boxRef, history, shown, g.plotW);

  if (!history || history.length === 0) {
    return (
      <EmptyState
        size="compact"
        title="No snapshots yet"
        description="Each index update records one, and the trend appears from the second."
      />
    );
  }

  const rows = at == null ? [] : readoutRows(history, at, shown);
  const x = at == null ? 0 : g.x(at);
  const drag = g.historyBands.length > 0 && shown.some((s) => s.key === "average_health");

  return (
    // No card. The chart sits inside a section that already names it, so a
    // border here is a second frame around content that has one.
    <div className="flex flex-col">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          KPI trend
        </h3>
        <Legend available={available} shown={shown} drag={drag} onToggle={toggle} onEmphasis={setEmphasis} />
      </div>
      <div
        ref={boxRef}
        tabIndex={0}
        role="group"
        aria-label="Health KPI trend. Arrow keys step through snapshots."
        {...box}
        className="relative rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-focus-ring)]"
      >
        <svg
          width={g.width}
          height={g.height}
          viewBox={`0 0 ${g.width} ${g.height}`}
          className="block max-w-full touch-pan-y"
          aria-hidden
          {...svg}
        >
          <Plot g={g} history={history} series={shown} lead={lead} emphasis={emphasis} />
          {at != null && <Crosshair g={g} x={x} rows={rows} />}
        </svg>
        {at != null && <Readout g={g} x={x} at={at} history={history} rows={rows} />}
        <p className="sr-only" aria-live="polite">
          {spoken}
        </p>
      </div>
    </div>
  );
}
