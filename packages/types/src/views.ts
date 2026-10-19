/**
 * Chat view parts: tables, charts and report sections a chat turn returns
 * next to its evidence cards.
 *
 * The model never writes a number into a view. A server-side builder makes
 * each table or chart from a tool result already in the turn, so every data
 * part names that result (`source`), says how its values are known (`basis`),
 * carries coverage when the tool reports one, and the index commit it was
 * read at (`as_of`). Like cards, parts are layout-free data that describe
 * changes and components, never people, and entities are refs, not URLs.
 *
 * A new kind is: its interface plus one `ViewPartsByKind` line here; its
 * module under `api-client/src/views/` plus one `VIEW_REGISTRY` line; its
 * model plus one `ViewPart` union line in `repowise.server.schemas.views`;
 * and its cases in `packages/types/fixtures/views.conformance.json`. Maps
 * keyed by `ViewKind` (validator, renderers) fail to compile until filled.
 * `schema_version` changes whenever a field is added or removed, because both
 * validators reject unknown fields; readers keep accepting the older versions
 * so stored parts still render, and a client meeting a chart it cannot draw
 * shows its table (`parseViewParts`).
 */

import type { CardBasis, CardRef } from "./evidence.js";

/** The version a server writes. */
export const VIEW_SCHEMA_VERSION = 2 as const;
/** Versions a reader accepts: 1 is every part stored before treemaps and heatmaps. */
export const VIEW_SCHEMA_VERSIONS = [1, 2] as const;
export type ViewSchemaVersion = (typeof VIEW_SCHEMA_VERSIONS)[number];

/** The chat-only tool that builds views. Its call is plumbing, not evidence. */
export const SHOW_VIEW_TOOL = "show_view";
/** The chat-only tool that charts a server dataset in one call; plumbing too. */
export const CHART_TOOL = "chart";
/** Tool calls whose result is a view, not evidence. */
export const VIEW_TOOLS: ReadonlySet<string> = new Set([SHOW_VIEW_TOOL, CHART_TOOL]);

/** Chart forms. A new form is added with its builder, never as a general grammar. */
export const CHART_TYPES = [
  "bar",
  "horizontal_bar",
  "stacked_bar",
  "line",
  "area",
  "donut",
  "scatter",
  "treemap",
  "heatmap",
] as const;
export type ChartType = (typeof CHART_TYPES)[number];

/** What a coverage ratio measures. */
export const VIEW_COVERAGE_METRICS = ["attribution", "test_reach"] as const;
export type ViewCoverageMetric = (typeof VIEW_COVERAGE_METRICS)[number];

export const CHART_SORT_ORDERS = ["asc", "desc"] as const;
export type ChartSortOrder = (typeof CHART_SORT_ORDERS)[number];

/**
 * Size caps; a payload over any of them is rejected, not cut. Strings have no
 * length cap beyond being non-empty; ids and column keys are ASCII patterns.
 */
export const VIEW_LIMITS = {
  /** Parts one turn or report may hold. */
  parts: 50,
  columns: 20,
  rows: 500,
  /** Rows a chart may plot. */
  chart_points: 100,
  chart_series: 8,
  /** Rows a donut needs at least and at most. */
  donut_min_parts: 2,
  donut_max_parts: 8,
  /** Nesting levels a treemap may name in `groups`. */
  treemap_groups: 3,
  narrative_blocks: 50,
  section_views: 20,
} as const;

/** The tool result a part was built from. */
export interface ViewSource {
  /** The artifact id the turn assigned to the tool result. */
  artifact_id: string;
  tool_name: string;
  /** Lowercase hex digest of the tool's arguments. */
  args_hash: string;
}

/** Share of the underlying data the tool could account for. */
export interface ViewCoverage {
  metric: ViewCoverageMetric;
  /** 0 to 1. */
  ratio: number;
}

export interface ViewPartBase<K extends string> {
  schema_version: ViewSchemaVersion;
  kind: K;
  /** Unique within one turn or report; sections and narrative cite parts by it. */
  id: string;
  /** Caption; a section's heading. */
  title: string;
  /** Plain text. */
  summary?: string;
}

/** Where a data part's values came from. Sections carry none of their own. */
export interface ViewProvenance {
  source: ViewSource;
  basis: CardBasis;
  coverage?: ViewCoverage;
  /** True when the tool reported its index as behind the code it read. */
  stale: boolean;
  /** Index commit the data was read at; null when the tool did not report one. */
  as_of: string | null;
}

export interface ViewColumn {
  /** Lowercase identifier; row objects are keyed by it. */
  key: string;
  label: string;
  unit?: string;
  /** Overrides the part's basis for this column. */
  basis?: CardBasis;
}

/**
 * One cell. A ref renders as a link to the entity; a number stays unformatted.
 * Every number on the wire is finite and within +/-(2^53 - 1), and integer
 * fields (`total_rows`, line numbers) are sent as JSON integers, never `5.0`.
 */
export type ViewCell = string | number | null | CardRef;

/** Rows keyed by column key; every row has exactly the columns' keys. */
export interface ViewTableData {
  columns: ViewColumn[];
  rows: Array<Record<string, ViewCell>>;
  /** Rows the source held; more than `rows.length` exactly when `truncated`. */
  total_rows: number;
  truncated: boolean;
}

export interface TableView extends ViewPartBase<"table">, ViewProvenance, ViewTableData {}

/**
 * A chart over its own table, which renderers also show as the data view.
 * `x` and every `series` entry are column keys; series values are numbers in
 * one shared unit. `sort` states the rows' order by the sum of their series
 * values (absent means source order) and is checked, not trusted. A scatter's
 * `x` is numeric too: each row is one point per series it has a value in.
 * A heatmap reads the same table as a grid: rows by `x`, one column per
 * series, each cell its value. A treemap sizes one cell per row by its one
 * series, nests cells by `groups` (column keys, outermost first) and may
 * colour them by `color` (a column key); both fields belong to treemaps only.
 */
export interface ChartView extends ViewPartBase<"chart">, ViewProvenance {
  chart_type: ChartType;
  data: ViewTableData;
  x: string;
  series: string[];
  sort?: ChartSortOrder;
  /** Required: a chart always comes with its text summary. */
  summary: string;
  groups?: string[];
  color?: string;
}

/** One paragraph of report narrative and the parts its facts rest on. */
export interface NarrativeBlock {
  text: string;
  /** Ids of parts in the same turn or report. */
  cites: string[];
}

/** A report section: a heading, narrative, and the data parts it shows, in order. */
export interface SectionView extends ViewPartBase<"section"> {
  narrative: NarrativeBlock[];
  /** Ids of table or chart parts in the same turn or report. */
  view_ids: string[];
}

/** The kind registry: one entry per view kind. */
export interface ViewPartsByKind {
  table: TableView;
  chart: ChartView;
  section: SectionView;
}
export type ViewKind = keyof ViewPartsByKind;
export type ViewPart = ViewPartsByKind[ViewKind];
