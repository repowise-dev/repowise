/**
 * The `chart` view kind. Charts plot their own table, so these rules only
 * check that the chart reads that table honestly: axes name real columns,
 * plotted values are numbers in one unit, a donut is a whole of two to eight
 * non-negative parts with a positive total, a stacked bar or treemap is not
 * all zeros, a scatter's x is a number, a stated sort order is the order the
 * rows are in (a sorted chart may not hold nulls in its series, since a null
 * has no place in the order), and only a treemap names `groups` or `color`.
 */

import {
  CHART_SORT_ORDERS,
  CHART_TYPES,
  VIEW_LIMITS,
  type ChartType,
  type ChartView,
} from "@repowise-dev/types/views";

import {
  duplicates,
  isFiniteNumber,
  isRec,
  list,
  obj,
  oneOf,
  str,
  type Rec,
  type Spec,
} from "../validation";
import { PROVENANCE_SPEC, baseSpec, type ViewKindEntry } from "./base";
import { columnKeys, tableData } from "./table";

/** Fewest and most series each chart type plots. */
const SERIES_BOUNDS: { [T in ChartType]: [number, number] } = {
  bar: [1, VIEW_LIMITS.chart_series],
  horizontal_bar: [1, VIEW_LIMITS.chart_series],
  line: [1, VIEW_LIMITS.chart_series],
  area: [1, VIEW_LIMITS.chart_series],
  stacked_bar: [2, VIEW_LIMITS.chart_series],
  donut: [1, 1],
  scatter: [1, VIEW_LIMITS.chart_series],
  treemap: [1, 1],
  heatmap: [1, VIEW_LIMITS.chart_series],
};
/** Chart types whose values are parts of a whole, so never negative. */
const PARTS_OF_A_WHOLE: ReadonlySet<ChartType> = new Set(["stacked_bar", "donut", "treemap"]);

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
  optional: {
    ...PROVENANCE_SPEC.optional,
    sort: oneOf(CHART_SORT_ORDERS),
    groups: list(str, VIEW_LIMITS.treemap_groups),
    color: str,
  },
};

function chartRules(v: Rec, p: string, e: string[]): void {
  const keyList = columnKeys(v.data);
  const data = v.data as Rec;
  if (keyList === null || !Array.isArray(data.rows) || !Array.isArray(v.series)) return;
  const keys = new Set(keyList);
  const rows = data.rows.filter(isRec);
  const series = v.series.filter((s): s is string => typeof s === "string");
  const plotted = series.filter((s) => keys.has(s) && s !== v.x);
  const type = v.chart_type as ChartType;

  if (typeof v.x === "string" && !keys.has(v.x)) e.push(`${p}.x: not a column of data`);
  treemapRules(v, keys, series, p, e);
  series.forEach((s, i) => {
    if (!keys.has(s)) e.push(`${p}.series[${i}]: not a column of data`);
    else if (s === v.x) e.push(`${p}.series[${i}]: is the x column`);
  });
  for (const s of duplicates(series)) e.push(`${p}.series: duplicate ${s}`);

  const bounds = SERIES_BOUNDS[type];
  if (bounds && (series.length < bounds[0] || series.length > bounds[1])) {
    const want = bounds[0] === bounds[1] ? `${bounds[0]}` : `${bounds[0]} to ${bounds[1]}`;
    e.push(`${p}.series: a ${type} chart plots ${want} series`);
  }
  if (rows.length > VIEW_LIMITS.chart_points) {
    e.push(`${p}.data.rows: a chart plots at most ${VIEW_LIMITS.chart_points} rows`);
  }

  const columns = new Map(
    (data.columns as unknown[]).filter(isRec).map((c) => [c.key, c] as const),
  );
  const units = new Set(plotted.map((s) => columns.get(s)?.unit ?? null));
  if (units.size > 1) e.push(`${p}.series: plotted columns must share one unit`);

  const sorted = v.sort === "asc" || v.sort === "desc";
  let total = 0;
  rows.forEach((r, i) => {
    if (typeof v.x === "string" && r[v.x] === null) e.push(`${p}.data.rows[${i}].${v.x}: x is null`);
    else if (type === "scatter" && typeof v.x === "string" && !isFiniteNumber(r[v.x])) {
      e.push(`${p}.data.rows[${i}].${v.x}: a scatter x is a number`);
    }
    for (const s of plotted) {
      const cell = r[s];
      if (cell === undefined) continue;
      if (cell === null) {
        if (type === "donut") e.push(`${p}.data.rows[${i}].${s}: a donut part is null`);
        else if (sorted) e.push(`${p}.data.rows[${i}].${s}: a sorted chart has no nulls`);
      } else if (!isFiniteNumber(cell)) {
        e.push(`${p}.data.rows[${i}].${s}: plotted values must be numbers`);
      } else if (cell < 0 && PARTS_OF_A_WHOLE.has(type)) {
        e.push(`${p}.data.rows[${i}].${s}: a ${type} value cannot be negative`);
      } else {
        total += cell;
      }
    }
  });

  if (PARTS_OF_A_WHOLE.has(type) && rows.length > 0 && total <= 0) {
    e.push(`${p}.data.rows: a ${type} needs a positive total`);
  }
  if (type === "donut") {
    const { donut_min_parts: min, donut_max_parts: max } = VIEW_LIMITS;
    if (rows.length < min || rows.length > max) {
      e.push(`${p}.data.rows: a donut has ${min} to ${max} parts`);
    }
    if (data.truncated === true) e.push(`${p}.data.truncated: a donut must show the whole`);
  }

  if (sorted) {
    const totals = rows.map((r) =>
      plotted.reduce((sum, s) => sum + (isFiniteNumber(r[s]) ? (r[s] as number) : 0), 0),
    );
    const sign = v.sort === "asc" ? 1 : -1;
    if (totals.some((t, i) => i > 0 && sign * (t - totals[i - 1]!) < 0)) {
      e.push(`${p}.sort: rows are not in ${v.sort} order of their series total`);
    }
  }
}

function treemapRules(v: Rec, keys: Set<unknown>, series: string[], p: string, e: string[]): void {
  if (v.groups === undefined && v.color === undefined) return;
  if (v.chart_type !== "treemap") {
    e.push(`${p}: groups and color apply to a treemap only`);
    return;
  }
  const groups = Array.isArray(v.groups) ? v.groups.filter((g): g is string => typeof g === "string") : [];
  groups.forEach((g, i) => {
    if (!keys.has(g)) e.push(`${p}.groups[${i}]: not a column of data`);
    else if (g === v.x || series.includes(g)) e.push(`${p}.groups[${i}]: is the x or a series column`);
  });
  for (const g of duplicates(groups)) e.push(`${p}.groups: duplicate ${g}`);
  if (typeof v.color === "string" && (!keys.has(v.color) || v.color === v.x)) {
    e.push(`${p}.color: not a column of data other than x`);
  }
}

export const CHART_KIND: ViewKindEntry<"chart"> = {
  check: obj(CHART_SPEC, chartRules),
  isData: true,
};
