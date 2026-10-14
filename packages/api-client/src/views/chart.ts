/**
 * The `chart` view kind. Charts plot their own table, so these rules only
 * check that the chart reads that table honestly: axes name real columns,
 * plotted values are numbers in one unit, a donut is a whole of two to five
 * non-negative parts, and a stated sort order is the order the rows are in.
 */

import {
  CHART_SORT_ORDERS,
  CHART_TYPES,
  VIEW_LIMITS,
  type ChartType,
  type ChartView,
} from "@repowise-dev/types/views";

import {
  isFiniteNumber,
  isRec,
  list,
  obj,
  oneOf,
  str,
  type Rec,
  type Spec,
} from "../validation";
import { PROVENANCE_SPEC, baseSpec, duplicates } from "./base";
import { columnKeys, tableData } from "./table";

/** Fewest and most series each chart type plots. */
const SERIES_BOUNDS: { [T in ChartType]: [number, number] } = {
  bar: [1, VIEW_LIMITS.chart_series],
  line: [1, VIEW_LIMITS.chart_series],
  stacked_bar: [2, VIEW_LIMITS.chart_series],
  donut: [1, 1],
};
/** Chart types whose values are parts of a whole, so never negative. */
const PARTS_OF_A_WHOLE: ReadonlySet<ChartType> = new Set(["stacked_bar", "donut"]);

const base = baseSpec("chart");
export const CHART_SPEC: Spec<ChartView> = {
  required: {
    ...base.required,
    ...PROVENANCE_SPEC.required,
    chart_type: oneOf(CHART_TYPES),
    data: tableData,
    x: str,
    series: list(str, VIEW_LIMITS.chart_series),
    summary: str,
  },
  optional: { ...PROVENANCE_SPEC.optional, sort: oneOf(CHART_SORT_ORDERS) },
};

function chartRules(v: Rec, p: string, e: string[]): void {
  const keys = columnKeys(v.data);
  const data = v.data as Rec;
  if (keys === null || !Array.isArray(data.rows) || !Array.isArray(v.series)) return;
  const rows = data.rows.filter(isRec);
  const series = v.series.filter((s): s is string => typeof s === "string");
  const type = v.chart_type as ChartType;

  if (typeof v.x === "string" && !keys.includes(v.x)) e.push(`${p}.x: not a column of data`);
  series.forEach((s, i) => {
    if (!keys.includes(s)) e.push(`${p}.series[${i}]: not a column of data`);
    else if (s === v.x) e.push(`${p}.series[${i}]: is the x column`);
  });
  for (const s of new Set(duplicates(series))) e.push(`${p}.series: duplicate ${String(s)}`);

  const bounds = SERIES_BOUNDS[type];
  if (bounds && (series.length < bounds[0] || series.length > bounds[1])) {
    const want = bounds[0] === bounds[1] ? `${bounds[0]}` : `${bounds[0]} to ${bounds[1]}`;
    e.push(`${p}.series: a ${type} chart plots ${want} series`);
  }
  if (rows.length > VIEW_LIMITS.chart_points) {
    e.push(`${p}.data.rows: a chart plots at most ${VIEW_LIMITS.chart_points} rows`);
  }

  const columns = (data.columns as unknown[]).filter(isRec);
  const units = new Set(
    series.map((s) => columns.find((c) => c.key === s)).map((c) => (c ? (c.unit ?? null) : null)),
  );
  if (units.size > 1) e.push(`${p}.series: plotted columns must share one unit`);

  rows.forEach((r, i) => {
    if (typeof v.x === "string" && r[v.x] === null) e.push(`${p}.data.rows[${i}].${v.x}: x is null`);
    for (const s of series) {
      const cell = r[s];
      if (cell === null || cell === undefined) {
        if (type === "donut" && cell === null) e.push(`${p}.data.rows[${i}].${s}: a donut part is null`);
      } else if (!isFiniteNumber(cell)) {
        e.push(`${p}.data.rows[${i}].${s}: plotted values must be numbers`);
      } else if (cell < 0 && PARTS_OF_A_WHOLE.has(type)) {
        e.push(`${p}.data.rows[${i}].${s}: a ${type} value cannot be negative`);
      }
    }
  });

  if (type === "donut") {
    const { donut_min_parts: min, donut_max_parts: max } = VIEW_LIMITS;
    if (rows.length < min || rows.length > max) {
      e.push(`${p}.data.rows: a donut has ${min} to ${max} parts`);
    }
    if (data.truncated === true) e.push(`${p}.data.truncated: a donut must show the whole`);
  }

  if (v.sort === "asc" || v.sort === "desc") {
    const totals = rows.map((r) =>
      series.reduce((sum, s) => sum + (isFiniteNumber(r[s]) ? (r[s] as number) : 0), 0),
    );
    const sign = v.sort === "asc" ? 1 : -1;
    if (totals.some((t, i) => i > 0 && sign * (t - totals[i - 1]!) < 0)) {
      e.push(`${p}.sort: rows are not in ${v.sort} order of their series total`);
    }
  }
}

export const chart = obj(CHART_SPEC, chartRules);
