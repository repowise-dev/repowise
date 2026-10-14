"use client";

/**
 * Chat view parts: the tables, charts and report sections a server-side
 * builder made from a tool result of the turn. The renderer draws exactly the
 * rows it is given: every chart keeps a text summary, a "Data" toggle opens
 * the table it plots, and the line under each part names where the values
 * came from. On paper the toggle goes and every chart prints with its table.
 */

import { useId, useState, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CardRef } from "@repowise-dev/types/cards";
import type {
  ChartView,
  SectionView,
  TableView,
  ViewCell,
  ViewColumn,
  ViewPart,
  ViewTableData,
} from "@repowise-dev/types/views";
import { BasisNote, RefView, refLabel, type RefHref } from "../cards/evidence-card";
import { cn } from "../lib/cn";

/**
 * Series fills in fixed order, never cycled: the share steps, ink stepped
 * toward the well, each clearing 3:1 on the page and a card. No hue, so a
 * series never reads as navigation, model work or a health verdict. Only
 * five clear the floor, so a chart plots its first five series and the
 * legend points to the data table for the rest.
 */
const SERIES_FILLS = [
  "var(--color-share-1)",
  "var(--color-share-2)",
  "var(--color-share-3)",
  "var(--color-share-4)",
  "var(--color-share-5)",
] as const;
/** Lines differ by dash as well as lightness, one pattern per fill. */
const LINE_DASHES = ["", "6 3", "2 3", "8 3 2 3", "1 2"] as const;

const seriesFill = (i: number): string => SERIES_FILLS[Math.min(i, SERIES_FILLS.length - 1)]!;
const lineDash = (i: number): string => LINE_DASHES[Math.min(i, LINE_DASHES.length - 1)]!;
/** The series a chart draws; the rest stay in its data table. */
const drawnSeries = (view: ChartView): string[] => view.series.slice(0, SERIES_FILLS.length);

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

function short(text: string, max = 22): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
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
            <tr key={i} className="border-b border-[var(--color-border-default)] last:border-0 print:break-inside-avoid">
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
      <BasisNote basis={part.basis} />
      <span>· {part.source.tool_name}</span>
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
    return view.data.rows.map((row, i) => ({
      label: cellText(row[view.x] ?? null),
      fill: seriesFill(i),
      value: row[key] ?? null,
    }));
  }
  if (view.series.length < 2) return [];
  const labels = new Map(view.data.columns.map((c) => [c.key, c.label]));
  return drawnSeries(view).map((key, i) => ({ label: labels.get(key) ?? key, fill: seriesFill(i) }));
}

function ChartLegend({ view }: { view: ChartView }) {
  const items = legendItems(view);
  const undrawn = view.chart_type === "donut" ? 0 : view.series.length - drawnSeries(view).length;
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-secondary)]">
      {items.map((item, i) => (
        <li key={i} className="inline-flex items-center gap-1.5">
          {view.chart_type === "line" ? (
            <svg aria-hidden width="16" height="6" className="shrink-0">
              <line x1="0" y1="3" x2="16" y2="3" stroke={item.fill} strokeWidth="2" strokeDasharray={lineDash(i)} />
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
  const series = drawnSeries(view);
  const labels = new Map(view.data.columns.map((c) => [c.key, c.label]));
  const rows = view.data.rows.map((row) => {
    const point: Record<string, string | number | null> = { [X_KEY]: cellText(row[view.x] ?? null) };
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
              stroke="var(--color-bg-root)"
              isAnimationActive={false}
            >
              {rows.map((_, i) => (
                <Cell key={i} fill={seriesFill(i)} />
              ))}
            </Pie>
            {tooltip}
          </PieChart>
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
                stroke={seriesFill(i)}
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
  // read along a row and not under a column.
  const stacked = view.chart_type === "stacked_bar";
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
              fill={seriesFill(i)}
              // A surface-coloured seam keeps stacked segments apart beyond their lightness.
              {...(stacked ? { stackId: "parts", stroke: "var(--color-bg-surface)", strokeWidth: 1 } : {})}
              maxBarSize={18}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
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
      className="m-0 space-y-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3 print:break-inside-avoid print:border-0 print:p-0"
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
      {/* The chart is a picture of the table below; the summary is its text. */}
      <div aria-hidden>
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
