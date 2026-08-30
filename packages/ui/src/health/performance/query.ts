import type {
  PerformanceActionabilityState,
  PerformanceContextFilter,
  PerformanceOpportunityConfidence,
  PerformanceOpportunityQuery,
} from "@repowise-dev/types/health";

/**
 * The queue's filter state, and its round trip to a query string.
 *
 * Every field here is a parameter the server answers, so narrowing is always a
 * refetch and never a filter over the loaded page. A page can be a slice of a
 * much larger result, so filtering it on the client would answer a different
 * question than the count beside it.
 */

export type PerformanceSort = NonNullable<PerformanceOpportunityQuery["sort"]>;

export interface PerformanceFilterState {
  context: PerformanceContextFilter;
  boundary: string | null;
  confidence: PerformanceOpportunityConfidence | null;
  actionability: PerformanceActionabilityState | null;
  sort: PerformanceSort;
  offset: number;
}

/**
 * Production is the default view. Most of what the analysis finds sits in
 * tests, benchmarks and CI scripts, and "this benchmark repeats work" is a
 * fact about a benchmark rather than work to schedule. Every other context
 * stays one tab away and keeps its count in the tab row.
 */
export const INITIAL_FILTERS: PerformanceFilterState = {
  context: "production",
  boundary: null,
  confidence: null,
  actionability: null,
  sort: "rank",
  offset: 0,
};

/** The narrowing filters, apart from context, which is a permanent tab row. */
const NARROWING_KEYS = ["boundary", "confidence", "actionability"] as const;
export type NarrowingKey = (typeof NARROWING_KEYS)[number];

const SORTS: readonly PerformanceSort[] = ["rank", "leverage", "observations"];
const CONFIDENCES: readonly PerformanceOpportunityConfidence[] = ["high", "medium", "low"];
const ACTIONABILITIES: readonly PerformanceActionabilityState[] = [
  "plan_ready",
  "advisory",
  "investigate",
];
const CONTEXTS: readonly PerformanceContextFilter[] = [
  "all",
  "production",
  "tooling",
  "test",
  "unknown",
];

/**
 * Build the server query. `collapseContexts` is set only when the server
 * predates the canonical vocabulary, and is the one place the retired
 * `production_tooling` spelling may be emitted.
 */
export function toQuery(
  state: PerformanceFilterState,
  options: { limit: number; collapseContexts?: boolean },
): PerformanceOpportunityQuery {
  const context =
    options.collapseContexts && (state.context === "production" || state.context === "tooling")
      ? ("production_tooling" as const)
      : state.context;
  const query: PerformanceOpportunityQuery = {
    context,
    view: "detail",
    sort: state.sort,
    limit: options.limit,
    offset: state.offset,
  };
  if (state.boundary) query.boundary = state.boundary;
  if (state.confidence) query.confidence = state.confidence;
  if (state.actionability) query.actionability = state.actionability;
  return query;
}

/**
 * A stable string for the state. Keys are written in a fixed order and
 * defaults are omitted, so an unchanged filter set always produces the same
 * cache key and the same shareable link. Context is the exception and is
 * always written, because it is the one default that decides which rows exist
 * rather than how they are shown.
 */
export function serializeFilters(state: PerformanceFilterState): string {
  const params = new URLSearchParams();
  // Always written, unlike the other defaults: a link that omits it would be
  // read under whatever the default is when it is opened, so a shared link
  // would change meaning if that default ever moved again.
  params.set("context", state.context);
  if (state.boundary) params.set("boundary", state.boundary);
  if (state.confidence) params.set("confidence", state.confidence);
  if (state.actionability) params.set("actionability", state.actionability);
  if (state.sort !== INITIAL_FILTERS.sort) params.set("sort", state.sort);
  if (state.offset > 0) params.set("offset", String(state.offset));
  return params.toString();
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return value && (allowed as readonly string[]).includes(value) ? (value as T) : null;
}

/**
 * Read the state back. An unrecognized value falls back to the default rather
 * than being forwarded to the server, so a hand-edited link cannot make the
 * queue look empty.
 */
export function parseFilters(search: string | URLSearchParams): PerformanceFilterState {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const offset = Number.parseInt(params.get("offset") ?? "", 10);
  return {
    context: oneOf(params.get("context"), CONTEXTS) ?? INITIAL_FILTERS.context,
    boundary: params.get("boundary") || null,
    confidence: oneOf(params.get("confidence"), CONFIDENCES),
    actionability: oneOf(params.get("actionability"), ACTIONABILITIES),
    sort: oneOf(params.get("sort"), SORTS) ?? INITIAL_FILTERS.sort,
    offset: Number.isFinite(offset) && offset > 0 ? offset : 0,
  };
}

/** Change one filter. Any narrowing change returns to the first page. */
export function withFilter<K extends keyof PerformanceFilterState>(
  state: PerformanceFilterState,
  key: K,
  value: PerformanceFilterState[K],
): PerformanceFilterState {
  if (key === "offset") return { ...state, offset: value as number };
  return { ...state, [key]: value, offset: 0 };
}

export function clearNarrowing(state: PerformanceFilterState): PerformanceFilterState {
  return { ...state, boundary: null, confidence: null, actionability: null, offset: 0 };
}

export function narrowingCount(state: PerformanceFilterState): number {
  return NARROWING_KEYS.filter((key) => state[key] !== null).length;
}
