"use client";

/**
 * Chat view parts: the tables, charts and report sections a server-side
 * builder made from a tool result of the turn. The renderer draws exactly the
 * rows it is given: every chart keeps a text summary, a "Data" toggle opens
 * the table it plots, and the line under each part names where the values
 * came from. On paper the toggle goes and every chart prints with its table.
 * Treemaps and heatmaps are drawn as HTML boxes, so their labels keep their
 * size at any width and need no measuring.
 */

import { useId, useState, type ReactNode } from "react";
import { hierarchy, treemap, type HierarchyRectangularNode } from "d3-hierarchy";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Symbols,
  Tooltip,
  XAxis,
  YAxis,
  type SymbolType,
} from "recharts";
import type { CardRef } from "@repowise-dev/types/cards";
import {
  VIEW_TOOLS,
  type ChartView,
  type SectionView,
  type TableView,
  type ViewCell,
  type ViewColumn,
  type ViewPart,
  type ViewTableData,
} from "@repowise-dev/types/views";
import { BasisNote, RefView, refLabel, type RefHref } from "../cards/evidence-card";
import { cn } from "../lib/cn";
import { SERIES, SERIES_TAIL, seriesColor } from "../shared/series";

/**
 * Series take the shared series palette in order. Lines also differ by dash
 * and scatter series by marker, one pattern each, so a chart plots its first
 * five series and the legend points to the data table for the rest.
 */
const LINE_DASHES = ["", "6 3", "2 3", "8 3 2 3", "1 2"] as const;
/** Scatter series differ by marker shape the same way. */
const POINT_SHAPES: readonly SymbolType[] = ["circle", "square", "triangle", "diamond", "cross"];

const lineDash = (i: number): string => LINE_DASHES[Math.min(i, LINE_DASHES.length - 1)]!;
const pointShape = (i: number): SymbolType => POINT_SHAPES[Math.min(i, POINT_SHAPES.length - 1)]!;
/** The series a chart draws; the rest stay in its data table. */
const drawnSeries = (view: ChartView): string[] => view.series.slice(0, LINE_DASHES.length);
/**
 * A donut's parts and a treemap's categories: one series colour each, then
 * the grey tail past the last colour, so no two named parts share a fill.
 * Every part is also named in the legend.
 */
const partFill = (i: number): string => (i < SERIES.length ? seriesColor(i) : SERIES_TAIL);

/**
 * Heatmap steps, light to dark (the heat tokens, an accent ramp). The two
 * deep steps carry card-coloured text and the light ones ink, each pair
 * clearing 4.5:1, so a value reads on its cell.
 */
const HEAT_STEPS = [
  { fill: "var(--color-heat-1)", ink: false },
  { fill: "var(--color-heat-2)", ink: false },
  { fill: "var(--color-heat-3)", ink: false },
  { fill: "var(--color-heat-4)", ink: true },
  { fill: "var(--color-heat-5)", ink: true },
] as const;

/** Health bands as fills, worst first: the health galaxy's node ramp. */
const BAND_FILLS: Record<string, string> = {
  "at risk": "var(--color-node-at-risk)",
  "needs work": "var(--color-node-needs-work)",
  fair: "var(--color-node-fair)",
  good: "var(--color-node-good)",
  excellent: "var(--color-node-excellent)",
};
/** The health engine's band floors (grading.py): a 0 to 10 score to its band. */
const BAND_FLOORS: Array<[number, string]> = [
  [8.5, "excellent"],
  [7, "good"],
  [5.5, "fair"],
  [4, "needs work"],
  [-Infinity, "at risk"],
];
const bandOf = (score: number): string => BAND_FLOORS.find(([floor]) => score >= floor)![1];

const COVERAGE_LABEL: Record<string, string> = {
  attribution: "Attribution coverage",
  test_reach: "Test reach coverage",
};

const AXIS_TICK = { fill: "var(--color-text-tertiary)", fontSize: 10 };
const TOOLTIP_STYLE = {
  background: "var(--color-bg-overlay)",
  border: "1px solid var(--color-border-default)",
  borderRadius: 6,
  fontSize: 12,
  color: "var(--color-text-primary)",
};
const X_KEY = "__label";
const BAR_ROW_PX = 24;
const PLOT_PX = 220;

const isRef = (cell: ViewCell): cell is CardRef => typeof cell === "object" && cell !== null;

function cellText(cell: ViewCell): string {
  if (cell === null) return "";
  return isRef(cell) ? refLabel(cell) : String(cell);
}

/** "Changes (commits)", unless the label already names its unit. */
function columnHeading(column: ViewColumn): string {
  const unit = column.unit;
  return unit && !column.label.toLowerCase().includes(unit.toLowerCase())
    ? `${column.label} (${unit})`
    : column.label;
}

/** A label cut to *max*; a path keeps its end, which is the part that names it. */
function short(text: string, max = 22): string {
  if (text.length <= max) return text;
  return text.includes("/") ? `…${text.slice(text.length - max + 1)}` : `${text.slice(0, max - 1)}…`;
}

/**
 * Axis labels for x cells. File refs show their shortest path suffix no
 * other file in the chart shares, so two `command.py` never read alike.
 */
export function axisLabels(cells: ViewCell[]): string[] {
  const paths = cells.map((cell) => (isRef(cell) && cell.type === "file" && !cell.start_line ? cell.id : null));
  const parts = paths.map((path) => path?.split("/") ?? []);
  return cells.map((cell, i) => {
    const own = parts[i]!;
    if (paths[i] === null) return cellText(cell);
    for (let k = 1; k <= own.length; k++) {
      const tail = own.slice(-k).join("/");
      const shared = parts.some((other, j) => j !== i && paths[j] !== null && other.slice(-k).join("/") === tail);
      if (!shared) return tail;
    }
    return paths[i]!;
  });
}

interface PartProps {
  buildRefHref?: RefHref | undefined;
  /** Page-unique DOM id for a part id; view ids repeat across turns. */
  domId: (partId: string) => string;
}

function CellText({ cell, buildRefHref }: { cell: ViewCell; buildRefHref?: RefHref | undefined }) {
  if (cell === null) {
    return (
      <span className="text-[var(--color-text-tertiary)]">
        <span aria-hidden>–</span>
        <span className="sr-only">none</span>
      </span>
    );
  }
  if (isRef(cell)) return <RefView cardRef={cell} buildRefHref={buildRefHref} />;
  return <>{String(cell)}</>;
}

export function ViewTable({
  data,
  caption,
  buildRefHref,
}: {
  data: ViewTableData;
  /** Accessible name; the visible title sits on the part. */
  caption: string;
  buildRefHref?: RefHref | undefined;
}) {
  return (
    <div className="max-w-full overflow-x-auto print:overflow-visible">
      <table className="w-full border-collapse text-xs">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-[var(--color-border-default)]">
            {data.columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                title={column.basis ? `${column.basis.kind} · ${column.basis.source}` : undefined}
                className="px-2 py-1.5 text-left font-medium text-[var(--color-text-secondary)] first:pl-0"
              >
                {columnHeading(column)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, i) => (
            <tr key={i} className="border-b border-[var(--color-table-divider)] last:border-0 print:break-inside-avoid">
              {data.columns.map((column) => {
                const cell = row[column.key] ?? null;
                return (
                  <td
                    key={column.key}
                    className={cn(
                      "px-2 py-1.5 align-top text-[var(--color-text-primary)] first:pl-0",
                      typeof cell === "number" && "text-right tabular-nums",
                    )}
                  >
                    <CellText cell={cell} buildRefHref={buildRefHref} />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Where the values came from, in one quiet line. */
function ProvenanceLine({ part }: { part: TableView | ChartView }) {
  const table: ViewTableData = part.kind === "chart" ? part.data : part;
  const coverage = part.coverage;
  return (
    <p data-view-provenance="" className="flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-[var(--color-text-tertiary)]">
      {part.basis.kind === "unresolved" ? (
        <span data-card-basis="unresolved">From {part.basis.source}</span>
      ) : (
        <BasisNote basis={part.basis} />
      )}
      {!VIEW_TOOLS.has(part.source.tool_name) && part.basis.source !== part.source.tool_name && (
        <span>· {part.source.tool_name}</span>
      )}
      {coverage && (
        <span data-view-coverage={coverage.metric}>
          · {COVERAGE_LABEL[coverage.metric] ?? coverage.metric} {Math.round(coverage.ratio * 100)}%
        </span>
      )}
      {table.truncated && (
        <span data-view-truncated="">
          · Showing {table.rows.length} of {table.total_rows} rows
        </span>
      )}
      {part.as_of && (
        <span title={part.as_of} className="font-mono">
          · as of {part.as_of.slice(0, 7)}
        </span>
      )}
      {part.stale && <span data-view-stale="">· Index behind the code it read</span>}
    </p>
  );
}

function legendItems(view: ChartView): Array<{ label: string; fill: string; value?: ViewCell }> {
  if (view.chart_type === "donut") {
    const key = view.series[0];
    if (key === undefined) return [];
    const labels = axisLabels(view.data.rows.map((row) => row[view.x] ?? null));
    return view.data.rows.map((row, i) => ({
      label: labels[i]!,
      fill: partFill(i),
      value: row[key] ?? null,
    }));
  }
  if (view.chart_type === "treemap") return treemapColors(view).legend;
  if (view.chart_type === "heatmap") {
    const { low, high } = heatRange(view);
    if (low === null || high === null) return [];
    return [
      { label: `${low} (lightest)`, fill: HEAT_STEPS[0].fill },
      { label: `${high} (darkest)`, fill: HEAT_STEPS[HEAT_STEPS.length - 1]!.fill },
    ];
  }
  if (view.series.length < 2) return [];
  const labels = new Map(view.data.columns.map((c) => [c.key, c.label]));
  return drawnSeries(view).map((key, i) => ({ label: labels.get(key) ?? key, fill: seriesColor(i) }));
}

function ChartLegend({ view }: { view: ChartView }) {
  const items = legendItems(view);
  const whole = ["donut", "treemap", "heatmap"].includes(view.chart_type);
  const undrawn = whole ? 0 : view.series.length - drawnSeries(view).length;
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-secondary)]">
      {items.map((item, i) => (
        <li key={i} className="inline-flex items-center gap-1.5">
          {view.chart_type === "line" || view.chart_type === "area" ? (
            <svg aria-hidden width="16" height="6" className="shrink-0">
              <line x1="0" y1="3" x2="16" y2="3" stroke={item.fill} strokeWidth="2" strokeDasharray={lineDash(i)} />
            </svg>
          ) : view.chart_type === "scatter" ? (
            <svg aria-hidden width="10" height="10" className="shrink-0">
              <Symbols cx={5} cy={5} size={48} type={pointShape(i)} fill={item.fill} />
            </svg>
          ) : (
            <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: item.fill }} />
          )}
          <span>{item.label}</span>
          {item.value !== undefined && (
            <span className="tabular-nums text-[var(--color-text-tertiary)]">{cellText(item.value)}</span>
          )}
        </li>
      ))}
      {undrawn > 0 && (
        <li data-view-undrawn={undrawn} className="text-[var(--color-text-tertiary)]">
          {undrawn} more series in the data table
        </li>
      )}
    </ul>
  );
}

function ChartPlot({ view }: { view: ChartView }) {
  if (view.chart_type === "treemap") return <TreemapPlot view={view} />;
  if (view.chart_type === "heatmap") return <HeatmapPlot view={view} />;
  const series = drawnSeries(view);
  const labels = new Map(view.data.columns.map((c) => [c.key, c.label]));
  const xLabels = axisLabels(view.data.rows.map((row) => row[view.x] ?? null));
  const rows = view.data.rows.map((row, i) => {
    const point: Record<string, string | number | null> = { [X_KEY]: xLabels[i]! };
    for (const key of series) {
      const value = row[key];
      point[key] = typeof value === "number" ? value : null;
    }
    return point;
  });
  const tooltip = (
    <Tooltip
      cursor={{ fill: "var(--color-bg-elevated)" }}
      contentStyle={TOOLTIP_STYLE}
      formatter={(value, name) => [String(value), labels.get(String(name)) ?? String(name)]}
    />
  );

  if (view.chart_type === "donut") {
    const key = series[0];
    if (key === undefined) return null;
    return (
      <div style={{ height: 180 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={rows}
              dataKey={key}
              nameKey={X_KEY}
              innerRadius="55%"
              outerRadius="85%"
              // Gaps, not a stroke, so the seams show whatever the chart sits on.
              stroke="none"
              paddingAngle={1}
              isAnimationActive={false}
            >
              {rows.map((_, i) => (
                <Cell key={i} fill={partFill(i)} />
              ))}
            </Pie>
            {tooltip}
          </PieChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (view.chart_type === "scatter") {
    // Both axes are numbers; each series plots the rows it has a value in.
    const plotted = series.map((key) => ({
      key,
      points: view.data.rows.flatMap((row) => {
        const x = row[view.x];
        const y = row[key];
        return typeof x === "number" && typeof y === "number" ? [{ x, y }] : [];
      }),
    }));
    return (
      <div style={{ height: PLOT_PX }}>
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="var(--color-border-default)" />
            <XAxis type="number" dataKey="x" name={labels.get(view.x) ?? view.x} tick={AXIS_TICK} axisLine={false} tickLine={false} />
            <YAxis
              type="number"
              dataKey="y"
              // A split's series are values of one field, so the tooltip calls them "Value".
              name={series.length === 1 ? (labels.get(series[0]!) ?? series[0]!) : "Value"}
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip cursor={{ strokeDasharray: "3 3" }} contentStyle={TOOLTIP_STYLE} />
            {plotted.map(({ key, points }, i) => (
              <Scatter
                key={key}
                name={labels.get(key) ?? key}
                data={points}
                fill={seriesColor(i)}
                shape={pointShape(i)}
                isAnimationActive={false}
              />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (view.chart_type === "area") {
    return (
      <div style={{ height: PLOT_PX }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="var(--color-border-default)" vertical={false} />
            <XAxis dataKey={X_KEY} tick={AXIS_TICK} axisLine={false} tickLine={false} tickFormatter={(v: string) => short(v, 14)} />
            <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} />
            {tooltip}
            {series.map((key, i) => (
              <Area
                key={key}
                type="monotone"
                dataKey={key}
                stroke={seriesColor(i)}
                strokeDasharray={lineDash(i)}
                strokeWidth={2}
                fill={seriesColor(i)}
                fillOpacity={0.18}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (view.chart_type === "line") {
    return (
      <div style={{ height: PLOT_PX }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="var(--color-border-default)" vertical={false} />
            <XAxis dataKey={X_KEY} tick={AXIS_TICK} axisLine={false} tickLine={false} tickFormatter={(v: string) => short(v, 14)} />
            <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} />
            {tooltip}
            {series.map((key, i) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                stroke={seriesColor(i)}
                strokeDasharray={lineDash(i)}
                strokeWidth={2}
                dot={rows.length <= 24}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // Bars run horizontally: their categories are components and paths, which
  // read along a row and not under a column. Over dates a bar is a column,
  // so time runs left to right as it does on a line.
  const stacked = view.chart_type === "stacked_bar";
  if (view.chart_type !== "horizontal_bar" && isTimeAxis(view)) {
    return (
      <div style={{ height: PLOT_PX }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="var(--color-border-default)" vertical={false} />
            <XAxis dataKey={X_KEY} tick={AXIS_TICK} axisLine={false} tickLine={false} tickFormatter={(v: string) => short(v, 14)} />
            <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} />
            {tooltip}
            {series.map((key, i) => (
              <Bar
                key={key}
                dataKey={key}
                fill={seriesColor(i)}
                {...(stacked ? { stackId: "parts" } : {})}
                maxBarSize={28}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }
  return (
    <div style={{ height: Math.max(96, rows.length * BAR_ROW_PX + 32) }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-border-default)" horizontal={false} />
          <XAxis type="number" tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <YAxis
            type="category"
            dataKey={X_KEY}
            tick={{ ...AXIS_TICK, fill: "var(--color-text-secondary)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={140}
            interval={0}
            tickFormatter={(v: string) => short(v)}
          />
          {tooltip}
          {series.map((key, i) => (
            <Bar
              key={key}
              dataKey={key}
              fill={seriesColor(i)}
              {...(stacked ? { stackId: "parts" } : {})}
              maxBarSize={18}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const TIME = /^\d{4}-\d{2}(-\d{2})?([T ]\d{2}:\d{2})?/;

/** True when every x cell is a date, so the chart reads left to right in time. */
function isTimeAxis(view: ChartView): boolean {
  const cells = view.data.rows.map((row) => row[view.x]);
  return cells.length > 0 && cells.every((cell) => typeof cell === "string" && TIME.test(cell));
}

function numbersIn(view: ChartView, keys: string[]): number[] {
  return view.data.rows.flatMap((row) => keys.map((k) => row[k]).filter((v): v is number => typeof v === "number"));
}

function heatRange(view: ChartView): { low: number | null; high: number | null } {
  const values = numbersIn(view, view.series);
  return values.length ? { low: Math.min(...values), high: Math.max(...values) } : { low: null, high: null };
}

function heatStep(value: number, low: number, high: number): (typeof HEAT_STEPS)[number] {
  const span = high - low;
  const at = span > 0 ? Math.min(HEAT_STEPS.length - 1, Math.floor(((value - low) / span) * HEAT_STEPS.length)) : HEAT_STEPS.length - 1;
  return HEAT_STEPS[at]!;
}

/**
 * A grid of rows by `x` and one column per series; every cell shows its value.
 * It is a table to assistive tech too (rows, headers, cells), since the
 * values sit in the cells and not only in the colour.
 */
function HeatmapPlot({ view }: { view: ChartView }) {
  const labels = new Map(view.data.columns.map((c) => [c.key, c.label]));
  const xLabels = axisLabels(view.data.rows.map((row) => row[view.x] ?? null));
  const { low, high } = heatRange(view);
  return (
    <div className="max-w-full overflow-x-auto">
      <div
        role="table"
        aria-label={view.title}
        className="grid gap-px text-[11px] tabular-nums"
        style={{ gridTemplateColumns: `minmax(6rem, 14rem) repeat(${view.series.length}, minmax(3rem, 1fr))` }}
      >
        <div role="row" className="contents">
          <span role="columnheader" className="truncate px-1 pb-1 text-[var(--color-text-tertiary)]">
            {labels.get(view.x) ?? view.x}
          </span>
          {view.series.map((key) => (
            <span
              key={key}
              role="columnheader"
              className="truncate px-1 pb-1 text-center text-[var(--color-text-tertiary)]"
              title={labels.get(key)}
            >
              {short(labels.get(key) ?? key, 14)}
            </span>
          ))}
        </div>
        {view.data.rows.map((row, i) => (
          <HeatRow key={i} label={xLabels[i]!} row={row} series={view.series} low={low} high={high} />
        ))}
      </div>
    </div>
  );
}

function HeatRow({
  label,
  row,
  series,
  low,
  high,
}: {
  label: string;
  row: Record<string, ViewCell>;
  series: string[];
  low: number | null;
  high: number | null;
}) {
  return (
    <div role="row" className="contents">
      <span role="rowheader" className="truncate py-1 pr-2 text-[var(--color-text-secondary)]" title={label}>
        {short(label, 30)}
      </span>
      {series.map((key) => {
        const value = row[key];
        if (typeof value !== "number" || low === null || high === null) {
          return (
            <span key={key} role="cell" className="rounded-sm bg-[var(--color-bg-inset)]">
              <span className="sr-only">none</span>
            </span>
          );
        }
        const step = heatStep(value, low, high);
        return (
          <span
            key={key}
            role="cell"
            data-heat-cell=""
            className="rounded-sm px-1 py-1 text-center"
            style={{ background: step.fill, color: step.ink ? "var(--color-bg-surface)" : "var(--color-text-primary)" }}
          >
            {value}
          </span>
        );
      })}
    </div>
  );
}

interface TreemapLeaf {
  name: string;
  size: number;
  color: string;
  path: string;
  /** What the fill stands for ("at risk", "7.2"), named on the cell, not only coloured. */
  tone: string | null;
}
interface TreemapBranch {
  name: string;
  children: Array<TreemapBranch | TreemapLeaf>;
}

/**
 * Each row's colour and the legend for it: health bands (as names, or a 0 to
 * 10 health score) take the health ramp; other text takes the series colours
 * in order of first appearance; other numbers take the heat steps.
 */
function treemapColors(view: ChartView): { fills: string[]; legend: Array<{ label: string; fill: string }> } {
  const plain = seriesColor(0);
  const key = view.color;
  if (!key) return { fills: view.data.rows.map(() => plain), legend: [] };
  const cells = view.data.rows.map((row) => row[key] ?? null);
  const label = view.data.columns.find((c) => c.key === key)?.label ?? key;
  const numbers = cells.filter((c): c is number => typeof c === "number");
  const isHealth = /health/i.test(`${key} ${label}`) && numbers.every((n) => n >= 0 && n <= 10);
  const names = cells.map((c) =>
    typeof c === "number" ? (isHealth ? bandOf(c) : null) : c === null ? null : cellText(c).replace(/_/g, " ").toLowerCase(),
  );
  if (names.every((n) => n === null || n in BAND_FILLS) && names.some((n) => n !== null)) {
    const present = Object.keys(BAND_FILLS).filter((band) => names.includes(band));
    return {
      fills: names.map((n) => (n ? BAND_FILLS[n]! : "var(--color-node-neutral)")),
      legend: present.map((band) => ({ label: band, fill: BAND_FILLS[band]! })),
    };
  }
  if (numbers.length === cells.filter((c) => c !== null).length && numbers.length > 0) {
    const low = Math.min(...numbers);
    const high = Math.max(...numbers);
    return {
      fills: cells.map((c) => (typeof c === "number" ? heatStep(c, low, high).fill : "var(--color-node-neutral)")),
      legend: [
        { label: `${label} ${low} (lightest)`, fill: HEAT_STEPS[0].fill },
        { label: `${label} ${high} (darkest)`, fill: HEAT_STEPS[HEAT_STEPS.length - 1]!.fill },
      ],
    };
  }
  const order = [...new Set(names.filter((n): n is string => n !== null))];
  const fillOf = (n: string | null) => (n === null ? "var(--color-node-neutral)" : partFill(order.indexOf(n)));
  return {
    fills: names.map(fillOf),
    legend: order.slice(0, SERIES.length).map((n) => ({ label: n, fill: fillOf(n) })),
  };
}

const TREEMAP_BOX = { width: 160, height: 100 };
const HALO = "0 0 2px var(--color-bg-surface), 0 0 3px var(--color-bg-surface)";

/** Cells sized by the one series, nested by `groups`, laid out once in percentages. */
function TreemapPlot({ view }: { view: ChartView }) {
  const sizeKey = view.series[0];
  if (sizeKey === undefined) return null;
  const { fills } = treemapColors(view);
  const xLabels = axisLabels(view.data.rows.map((row) => row[view.x] ?? null));
  const root: TreemapBranch = { name: "", children: [] };
  view.data.rows.forEach((row, i) => {
    const size = row[sizeKey];
    if (typeof size !== "number" || size <= 0) return;
    let node = root;
    for (const group of view.groups ?? []) {
      const name = cellText(row[group] ?? null) || "none";
      let next = node.children.find((c): c is TreemapBranch => "children" in c && c.name === name);
      if (!next) {
        next = { name, children: [] };
        node.children.push(next);
      }
      node = next;
    }
    const tone = view.color ? cellText(row[view.color] ?? null) || null : null;
    node.children.push({ name: xLabels[i]!, size, color: fills[i]!, path: cellText(row[view.x] ?? null), tone });
  });
  const laid = treemap<TreemapBranch | TreemapLeaf>()
    .size([TREEMAP_BOX.width, TREEMAP_BOX.height])
    .paddingInner(0.4)
    .paddingTop((node) => (node.depth > 0 && node.children ? 3 : 0))(
    hierarchy<TreemapBranch | TreemapLeaf>(root, (d) => ("children" in d ? d.children : undefined))
      .sum((d) => ("size" in d ? d.size : 0))
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0)),
  );
  const box = (n: HierarchyRectangularNode<TreemapBranch | TreemapLeaf>) => ({
    left: `${(n.x0 / TREEMAP_BOX.width) * 100}%`,
    top: `${(n.y0 / TREEMAP_BOX.height) * 100}%`,
    width: `${((n.x1 - n.x0) / TREEMAP_BOX.width) * 100}%`,
    height: `${((n.y1 - n.y0) / TREEMAP_BOX.height) * 100}%`,
  });
  return (
    <div className="relative w-full overflow-hidden rounded-md" style={{ height: 260 }}>
      {laid.descendants().map((node, i) => {
        if (node.depth === 0) return null;
        const d = node.data;
        if ("children" in d) {
          return (
            <div
              key={`g${i}`}
              data-treemap-group=""
              className="absolute truncate px-1 text-[10px] leading-3 text-[var(--color-text-tertiary)]"
              style={box(node)}
              title={`${d.name}: ${node.value}`}
            >
              {d.name}
            </div>
          );
        }
        const wide = node.x1 - node.x0 > 14 && node.y1 - node.y0 > 6;
        const tall = wide && node.y1 - node.y0 > 12;
        return (
          <div
            key={`l${i}`}
            data-treemap-cell=""
            className="absolute overflow-hidden px-1 text-[10px] leading-3 text-[var(--color-text-primary)]"
            // A card-coloured halo keeps the label readable on any fill.
            style={{ ...box(node), background: d.color, textShadow: HALO }}
            title={`${d.path}: ${d.size}${d.tone ? `, ${d.tone}` : ""}`}
          >
            {wide ? short(d.name, 24) : null}
            {tall && d.tone ? <span className="block">{d.tone}</span> : null}
          </div>
        );
      })}
    </div>
  );
}

function PartFrame({
  part,
  domId,
  children,
}: {
  part: TableView | ChartView;
  domId: (partId: string) => string;
  children: ReactNode;
}) {
  return (
    <figure
      id={domId(part.id)}
      data-view-kind={part.kind}
      data-view-id={part.id}
      {...(part.kind === "chart" ? { "data-chart-type": part.chart_type } : {})}
      // No card of its own: the part sits on the transcript like the prose around it.
      className="m-0 space-y-2 py-1 print:break-inside-avoid print:p-0"
    >
      <figcaption className="space-y-0.5">
        <span className="block text-sm font-medium text-[var(--color-text-primary)]">{part.title}</span>
        {part.summary && <span className="block text-sm text-[var(--color-text-secondary)]">{part.summary}</span>}
      </figcaption>
      {children}
      <ProvenanceLine part={part} />
    </figure>
  );
}

function ChartPart({ view, buildRefHref, domId }: PartProps & { view: ChartView }) {
  const [showData, setShowData] = useState(false);
  const tableId = `${domId(view.id)}-data`;
  return (
    <PartFrame part={view} domId={domId}>
      <ChartLegend view={view} />
      {/* The chart is a picture of the table below; the summary is its text. A
          heatmap is a table itself, with its values in its cells. */}
      <div aria-hidden={view.chart_type === "heatmap" ? undefined : true}>
        <ChartPlot view={view} />
      </div>
      <button
        type="button"
        aria-expanded={showData}
        aria-controls={tableId}
        onClick={() => setShowData((open) => !open)}
        className="inline-flex min-h-8 items-center rounded-md text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] print:hidden"
      >
        Data
      </button>
      {/* Always mounted so print shows it; hidden on screen until asked for. */}
      <div id={tableId} data-view-data="" className={cn(!showData && "hidden print:block")}>
        <ViewTable data={view.data} caption={view.title} buildRefHref={buildRefHref} />
      </div>
    </PartFrame>
  );
}

function TablePart({ view, buildRefHref, domId }: PartProps & { view: TableView }) {
  return (
    <PartFrame part={view} domId={domId}>
      <ViewTable data={view} caption={view.title} buildRefHref={buildRefHref} />
    </PartFrame>
  );
}

function DataPart({ view, ...props }: PartProps & { view: TableView | ChartView }) {
  return view.kind === "chart" ? <ChartPart view={view} {...props} /> : <TablePart view={view} {...props} />;
}

function SectionPart({
  section,
  parts,
  ...props
}: PartProps & { section: SectionView; parts: Map<string, TableView | ChartView> }) {
  return (
    <section data-view-kind="section" data-view-id={section.id} className="space-y-3">
      <h3 className="text-base font-semibold text-[var(--color-text-primary)] print:break-after-avoid">{section.title}</h3>
      {section.summary && <p className="text-sm text-[var(--color-text-secondary)]">{section.summary}</p>}
      {section.narrative.map((block, i) => (
        <p key={i} className="text-sm leading-relaxed text-[var(--color-text-primary)]">
          {block.text}
          {block.cites.map((id) => (
            <a
              key={id}
              href={`#${props.domId(id)}`}
              aria-label={`See ${parts.get(id)?.title ?? id}`}
              className="ml-1 align-super text-[10px] text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
            >
              {id}
            </a>
          ))}
        </p>
      ))}
      {section.view_ids.map((id) => {
        const part = parts.get(id);
        return part ? <DataPart key={id} view={part} {...props} /> : null;
      })}
    </section>
  );
}

/**
 * A turn's parts in order. A data part a section shows renders inside that
 * section only; the others render where they stand.
 */
export function ViewPartList({ views, buildRefHref }: { views: ViewPart[]; buildRefHref?: RefHref }) {
  const scope = useId();
  if (views.length === 0) return null;
  const domId = (partId: string) => `view-${scope.replace(/:/g, "")}-${partId}`;
  const data = new Map<string, TableView | ChartView>();
  const inSection = new Set<string>();
  for (const part of views) {
    if (part.kind === "section") part.view_ids.forEach((id) => inSection.add(id));
    else data.set(part.id, part);
  }
  const props = { buildRefHref, domId };
  return (
    <div data-view-list="" className="space-y-3">
      {views.map((part) => {
        if (part.kind === "section") return <SectionPart key={part.id} section={part} parts={data} {...props} />;
        return inSection.has(part.id) ? null : <DataPart key={part.id} view={part} {...props} />;
      })}
    </div>
  );
}
