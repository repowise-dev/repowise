import { PERFORMANCE_EXECUTION_ROLES } from "@repowise-dev/types/health";
import type {
  PerformanceActionabilityState,
  PerformanceCostProof,
  PerformanceExecutionContext,
  PerformanceExecutionRole,
  PerformanceOpportunityConfidence,
  PerformanceOpportunityQuery,
  PerformanceScope,
} from "@repowise-dev/types/health";

/**
 * The tab's view state, its server query, and its round trip to the URL.
 *
 * One scope decides which causes exist (worth doing now, every production
 * cause, everything), and every number on the page is counted inside it. The
 * filters narrow within the scope and are all server-side, so a count and the
 * rows it describes never come from two different universes, the area
 * included.
 */

export type { PerformanceScope };
export type PerformanceSort = NonNullable<PerformanceOpportunityQuery["sort"]>;

export interface PerformanceFilterState {
  scope: PerformanceScope;
  /** One execution context, inside Everything only. */
  context: PerformanceExecutionContext | null;
  boundary: string | null;
  confidence: PerformanceOpportunityConfidence | null;
  /** The actionability bar's selection. */
  actionability: PerformanceActionabilityState | null;
  /** Outside "now", whose causes are all measured and served. */
  proof: PerformanceCostProof | null;
  role: PerformanceExecutionRole | null;
  sort: PerformanceSort;
  /** 1-based page of `PAGE_SIZE` causes. */
  page: number;
  /** One area (a directory) at any depth, narrowed on the server. */
  area: string | null;
  /** The directory depth area cards are drawn at; null lets the server choose. */
  depth: number | null;
}

/** The server's page ceiling: a default queue fits in one page almost always. */
export const PAGE_SIZE = 100;

export const INITIAL_FILTERS: PerformanceFilterState = {
  scope: "now",
  context: null,
  boundary: null,
  confidence: null,
  actionability: null,
  proof: null,
  role: null,
  sort: "rank",
  page: 1,
  area: null,
  depth: null,
};

export const SCOPES: readonly PerformanceScope[] = ["now", "production", "all"];

/** The states the default queue holds. */
const NOW_STATES: readonly PerformanceActionabilityState[] = ["plan_ready", "advisory"];

/** The filters behind "More filters", each offered only where it can narrow. */
export const NARROWING_KEYS = ["context", "boundary", "confidence", "proof", "role"] as const;
export type NarrowingKey = (typeof NARROWING_KEYS)[number];

export function narrowingKeysFor(scope: PerformanceScope): readonly NarrowingKey[] {
  if (scope === "now") return ["boundary", "confidence"];
  if (scope === "production") return ["boundary", "confidence", "proof", "role"];
  return NARROWING_KEYS;
}

const SORTS: readonly PerformanceSort[] = ["rank", "leverage", "observations"];
const CONFIDENCES: readonly PerformanceOpportunityConfidence[] = ["high", "medium", "low"];
const ACTIONABILITIES: readonly PerformanceActionabilityState[] = [
  "plan_ready",
  "advisory",
  "investigate",
  "expected",
];
const PROOFS: readonly PerformanceCostProof[] = ["proven", "unproven", "background_unproven"];
const ROLES: readonly PerformanceExecutionRole[] = PERFORMANCE_EXECUTION_ROLES;
const CONTEXTS: readonly PerformanceExecutionContext[] = [
  "production",
  "tooling",
  "test",
  "unknown",
];

/**
 * Build the server query. Rows use the `summary` view, which carries what a
 * row is acted on by; plan steps and validation wait for the drawer.
 * `collapseContexts` is set only for a server that predates the canonical
 * vocabulary, and is the one place the retired `production_tooling` may go.
 */
export function toQuery(
  state: PerformanceFilterState,
  options: { limit: number; collapseContexts?: boolean },
): PerformanceOpportunityQuery {
  const query: PerformanceOpportunityQuery = {
    view: "summary",
    sort: state.sort,
    limit: options.limit,
    offset: (state.page - 1) * options.limit,
  };
  if (state.scope === "now") {
    query.context = "production";
    // A state chosen inside the default queue stays inside it.
    if (state.actionability) {
      query.actionability = state.actionability;
      query.queue = true;
    }
  } else {
    query.context = state.scope === "all" ? (state.context ?? "all") : "production";
    query.actionability = state.actionability ?? "all";
    query.proof = state.proof ?? "all";
    query.role = state.role ?? "all";
  }
  if (
    options.collapseContexts &&
    (query.context === "production" || query.context === "tooling")
  ) {
    query.context = "production_tooling";
  }
  if (state.boundary) query.boundary = state.boundary;
  if (state.confidence) query.confidence = state.confidence;
  if (state.area) query.area = state.area;
  if (state.depth) query.area_depth = state.depth;
  return query;
}

/** A stable string for the server half of the state: the queue's cache key. */
export function serverKey(state: PerformanceFilterState): string {
  return [
    state.scope,
    state.context ?? "",
    state.boundary ?? "",
    state.confidence ?? "",
    state.actionability ?? "",
    state.proof ?? "",
    state.role ?? "",
    state.sort,
    state.page,
    state.area ?? "",
    state.depth ?? "",
  ].join("|");
}

/** URL parameters retired by the flat scope schema, still read so old links open. */
export const LEGACY_PARAMS = ["perf", "ctx", "state"] as const;

/**
 * Write the state onto `params` in place. `scope` is always written: a link
 * that omits it would be read under whatever the default is when it opens.
 */
export function writeFilters(state: PerformanceFilterState, params: URLSearchParams): void {
  for (const legacy of LEGACY_PARAMS) params.delete(legacy);
  params.set("scope", state.scope);
  for (const key of [
    "context",
    "boundary",
    "confidence",
    "actionability",
    "proof",
    "role",
  ] as const) {
    const value = state[key];
    if (value) params.set(key, value);
    else params.delete(key);
  }
  if (state.sort !== INITIAL_FILTERS.sort) params.set("sort", state.sort);
  else params.delete("sort");
  if (state.page > 1) params.set("page", String(state.page));
  else params.delete("page");
  // The root area is the empty string, so only null means "no area".
  if (state.area !== null) params.set("area", state.area);
  else params.delete("area");
  if (state.depth) params.set("depth", String(state.depth));
  else params.delete("depth");
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return value && (allowed as readonly string[]).includes(value) ? (value as T) : null;
}

function positive(value: string | null): number | null {
  const n = Number.parseInt(value ?? "", 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** The narrowing fields every spelling shares. */
function narrowing(params: URLSearchParams) {
  return {
    boundary: params.get("boundary") || null,
    confidence: oneOf(params.get("confidence"), CONFIDENCES),
    actionability:
      oneOf(params.get("actionability"), ACTIONABILITIES) ??
      oneOf(params.get("state"), ACTIONABILITIES),
    proof: oneOf(params.get("proof"), PROOFS),
    role: oneOf(params.get("role"), ROLES),
    sort: oneOf(params.get("sort"), SORTS) ?? INITIAL_FILTERS.sort,
  };
}

/**
 * A link minted before scopes named a context (`ctx`, or `context` inside the
 * `perf` blob). Production with no widening filter was the default queue;
 * production with one is the production scope; anything else is Everything.
 */
function fromLegacy(params: URLSearchParams, contextKey: string): PerformanceFilterState {
  const fields = narrowing(params);
  const context = params.get(contextKey);
  const page = positive(params.get("page")) ?? Math.floor((positive(params.get("offset")) ?? 0) / PAGE_SIZE) + 1;
  const widened =
    fields.proof !== null ||
    fields.role !== null ||
    params.get("role") === "all" ||
    fields.actionability === "investigate" ||
    fields.actionability === "expected";
  const scope: PerformanceScope =
    context && context !== "production" ? "all" : widened ? "production" : "now";
  return {
    ...INITIAL_FILTERS,
    ...fields,
    scope,
    context: scope === "all" ? oneOf(context, CONTEXTS) : null,
    proof: scope === "now" ? null : fields.proof,
    role: scope === "now" ? null : fields.role,
    page,
    area: params.get("area"),
    depth: positive(params.get("depth")),
  };
}

/**
 * Read the state back from the page's parameters. An unrecognized value falls
 * back to the default rather than reaching the server, so a hand-edited link
 * cannot make the queue look empty.
 */
export function readFilters(params: URLSearchParams): PerformanceFilterState {
  if (!params.has("scope")) {
    const blob = params.get("perf");
    if (blob !== null && !params.has("ctx")) return fromLegacy(new URLSearchParams(blob), "context");
    if (params.has("ctx")) return fromLegacy(params, "ctx");
  }
  const scope = oneOf(params.get("scope"), SCOPES) ?? INITIAL_FILTERS.scope;
  const fields = narrowing(params);
  return {
    ...fields,
    // "Worth doing now" holds plan-ready and advisory causes only.
    actionability:
      scope === "now" && !NOW_STATES.includes(fields.actionability ?? "plan_ready")
        ? null
        : fields.actionability,
    scope,
    context: scope === "all" ? oneOf(params.get("context"), CONTEXTS) : null,
    proof: scope === "now" ? null : fields.proof,
    role: scope === "now" ? null : fields.role,
    page: positive(params.get("page")) ?? 1,
    area: params.get("area"),
    depth: positive(params.get("depth")),
  };
}

/** A host string that predates the flat schema is the `perf` blob itself. */
export function parseLegacyFilters(blob: string): PerformanceFilterState {
  return fromLegacy(new URLSearchParams(blob), "context");
}

/**
 * Change one field. Every filter is the server's, so any change but the page
 * returns to the first page. A new area draws its cards at the depth the
 * server chooses inside it.
 */
export function withFilter<K extends keyof PerformanceFilterState>(
  state: PerformanceFilterState,
  key: K,
  value: PerformanceFilterState[K],
): PerformanceFilterState {
  if (key === "page") return { ...state, page: value as number };
  if (key === "scope") return withScope(state, value as PerformanceScope);
  if (key === "area") return withFilters(state, { area: value as string | null, depth: null });
  return withFilters(state, { [key]: value });
}

/** Apply several server filters as one change. */
export function withFilters(
  state: PerformanceFilterState,
  patch: Partial<PerformanceFilterState>,
): PerformanceFilterState {
  return { ...state, ...patch, page: 1 };
}

/**
 * Move to another scope, keeping the filters that still narrow there and
 * dropping the actionability selection, whose counts belong to the old scope.
 */
export function withScope(
  state: PerformanceFilterState,
  scope: PerformanceScope,
  patch: Partial<PerformanceFilterState> = {},
): PerformanceFilterState {
  const kept = narrowingKeysFor(scope);
  const next: PerformanceFilterState = { ...INITIAL_FILTERS, scope, sort: state.sort };
  for (const key of kept) (next as unknown as Record<string, unknown>)[key] = state[key];
  return { ...next, ...patch };
}

export function clearNarrowing(state: PerformanceFilterState): PerformanceFilterState {
  return withFilters(state, {
    area: null,
    depth: null,
    context: null,
    boundary: null,
    confidence: null,
    actionability: null,
    proof: null,
    role: null,
  });
}

/** Active narrowing filters, the actionability selection included. */
export function narrowingCount(state: PerformanceFilterState): number {
  return (
    NARROWING_KEYS.filter((key) => state[key] !== null).length +
    (state.actionability !== null ? 1 : 0)
  );
}
