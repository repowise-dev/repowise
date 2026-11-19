/**
 * The commit view's history filters as URL state: health impact, hotspots,
 * time window, author and area. Pure, so the server page and the client
 * filter bar read and write the same query string.
 *
 * URL: `?health=better|worse|none&hotspots=1&window=week|quarter|all|custom`
 * `&since=YYYY-MM-DD&until=YYYY-MM-DD&author=&area=`. The past month is the
 * default window and stays out of the URL, so `/commits` and
 * `/commits?commit=<sha>` keep working as before.
 */
import type { DeltaFilter, HistoryFilters } from "@repowise-dev/types/history";
import type {
  HealthImpact,
  HistoryFilterValue,
  HistoryWindow,
} from "@repowise-dev/ui/commits/history-filters";

type Params = Record<string, string | string[] | undefined>;

const IMPACTS: readonly HealthImpact[] = ["all", "better", "worse", "none"];
const WINDOWS: readonly HistoryWindow[] = ["week", "month", "quarter", "all", "custom"];
const WINDOW_DAYS: Partial<Record<HistoryWindow, number>> = { week: 7, month: 30, quarter: 90 };
const DEFAULT_WINDOW: HistoryWindow = "month";
const DAY_MS = 86_400_000;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** A real calendar day: "2026-02-31" has the shape but not the day, and would
 *  throw when turned into an ISO range. */
function isDay(v: string | undefined): v is string {
  if (!v || !DATE.test(v)) return false;
  const t = Date.parse(`${v}T00:00:00Z`);
  return Number.isFinite(t) && new Date(t).toISOString().startsWith(v);
}

/** `improved` and `worsened` include mixed on the server, matching the
 *  headline's "made things better / worse" counts. */
const IMPACT_DELTA: Record<Exclude<HealthImpact, "all">, DeltaFilter> = {
  better: "improved",
  worse: "worsened",
  none: "neutral",
};

function one(v: string | string[] | undefined): string | undefined {
  const s = Array.isArray(v) ? v[0] : v;
  return s && s.trim() ? s.trim() : undefined;
}

function pick<T extends string>(v: string | undefined, allowed: readonly T[], fallback: T): T {
  return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
}

export function parseCommitFilters(sp: Params): HistoryFilterValue {
  const since = one(sp.since);
  const until = one(sp.until);
  return {
    impact: pick(one(sp.health), IMPACTS, "all"),
    hotspots: one(sp.hotspots) === "1",
    window: pick(one(sp.window), WINDOWS, DEFAULT_WINDOW),
    since: isDay(since) ? since : undefined,
    until: isDay(until) ? until : undefined,
    author: one(sp.author),
    area: one(sp.area),
  };
}

/** Today's UTC midnight: a rolling window keyed on the day, so the server's
 *  render and every refetch that day ask for the same range. */
function utcDay(now: Date): number {
  return Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
}

/** The ISO range a window covers; `until` is exclusive. */
export function windowRange(
  f: Pick<HistoryFilterValue, "window" | "since" | "until">,
  now: Date,
): { since?: string; until?: string } {
  if (f.window === "custom") {
    return {
      ...(f.since ? { since: `${f.since}T00:00:00Z` } : {}),
      // The picker's "to" day is inclusive, the API's `until` is not.
      ...(f.until ? { until: new Date(Date.parse(`${f.until}T00:00:00Z`) + DAY_MS).toISOString() } : {}),
    };
  }
  const days = WINDOW_DAYS[f.window];
  return days ? { since: new Date(utcDay(now) - days * DAY_MS).toISOString() } : {};
}

/** The API filters. `impact` is left out with `withImpact: false`, for the
 *  summary that counts every impact so the quick filters keep their sizes. */
export function historyFilters(
  f: HistoryFilterValue,
  now: Date,
  { withImpact = true }: { withImpact?: boolean } = {},
): HistoryFilters {
  return {
    ...windowRange(f, now),
    ...(f.author ? { author: f.author } : {}),
    ...(f.area ? { area: f.area } : {}),
    ...(f.hotspots ? { touches_hotspot: true } : {}),
    ...(withImpact && f.impact !== "all" ? { delta: IMPACT_DELTA[f.impact] } : {}),
  };
}

/** The window the range really covers: a custom window with no dates yet is
 *  the whole history, and is named so. */
export function effectiveWindow(f: Pick<HistoryFilterValue, "window" | "since" | "until">): HistoryWindow {
  return f.window === "custom" && !f.since && !f.until ? "all" : f.window;
}

/** True when anything but the whole history is shown. */
export function isNarrowed(f: HistoryFilterValue): boolean {
  return effectiveWindow(f) !== "all" || f.impact !== "all" || f.hotspots || !!f.author || !!f.area;
}

/**
 * `current` with `patch` applied, as a query string (no leading `?`). Other
 * parameters (`commit`, `view`) are kept; defaults are dropped so a reset
 * filter leaves no trace in the URL.
 */
export function commitFiltersQuery(current: URLSearchParams, patch: Partial<HistoryFilterValue>): string {
  const next = { ...parseCommitFilters(Object.fromEntries(current)), ...patch };
  const out = new URLSearchParams(current);
  const set = (k: string, v: string | undefined) => (v ? out.set(k, v) : out.delete(k));
  set("health", next.impact === "all" ? undefined : next.impact);
  set("hotspots", next.hotspots ? "1" : undefined);
  set("window", next.window === DEFAULT_WINDOW ? undefined : next.window);
  set("since", next.window === "custom" ? next.since : undefined);
  set("until", next.window === "custom" ? next.until : undefined);
  set("author", next.author);
  set("area", next.area);
  return out.toString();
}
