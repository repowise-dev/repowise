/**
 * Number, date, token, and other formatters for repowise UI.
 */

/** A timestamp we can render. */
type Timestamp = string | Date;

/**
 * Parse an API timestamp into a `Date`, treating a bare ISO string without an
 * explicit timezone suffix (e.g. `"2026-08-21T13:23:33"`) as UTC. The repowise
 * API returns UTC wall-clock strings with no trailing `Z`, so a plain
 * `new Date("...")` would interpret them as *local* time and shift every
 * displayed timestamp by the viewer's UTC offset.
 */
export function parseDate(date: Timestamp): Date {
  if (date instanceof Date) return date;
  // A date-only string already parses as UTC, and "2026-06-23Z" is invalid in Safari.
  if (/^\d{4}-\d{2}-\d{2}$/.test(date.trim())) return new Date(date.trim());
  // No timezone suffix (`Z`/`z` or `+HH:MM`)? Treat the wall-clock as UTC.
  if (!/[zZ]|[+-]\d\d?:\d\d$/.test(date.trim())) {
    return new Date(`${date.trim()}Z`);
  }
  return new Date(date);
}

const NUMBER_FORMAT = new Intl.NumberFormat("en-US");

/** Format a number with commas: 1234567 → "1,234,567".
 *  Pinned to en-US like `formatDate`: the runtime default differs between the
 *  server and the browser (en-IN groups 1261599 as 12,61,599), which broke
 *  hydration on every server-rendered figure over 99,999. */
export function formatNumber(n: number): string {
  return NUMBER_FORMAT.format(n);
}

/** English ordinal of an integer: 1 → "1st", 2 → "2nd", 3 → "3rd", 11 → "11th", 93 → "93rd". */
export function ordinal(n: number): string {
  const tens = Math.abs(n) % 100;
  const suffix =
    tens >= 11 && tens <= 13 ? "th" : (["th", "st", "nd", "rd"][Math.abs(n) % 10] ?? "th");
  return `${n}${suffix}`;
}

/** Format large counts compactly: 1234567 → "1.2M", 98432 → "98.4K", 999 → "999" */
export function formatCompact(n: number): string {
  if (n >= 1_000_000) return `${Number((n / 1_000_000).toFixed(1))}M`;
  if (n >= 1_000) {
    const thousands = Number((n / 1_000).toFixed(1));
    return thousands === 1_000 ? "1M" : `${thousands}K`;
  }
  return String(n);
}

/** Format token counts: 4200000 → "4.2M", 980000 → "980K", 1234 → "1,234" */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return formatNumber(n);
}

/** Format USD cost: 0.004 → "<$0.01", 4.2 → "$4.20", 18.6 → "$18.60" */
export function formatCost(usd: number): string {
  if (usd > 0 && usd < 0.01) return "<$0.01";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(usd);
}

export type RelativeTimeUnit = "now" | "s" | "m" | "h" | "d" | "w" | "mo" | "y";

/** The bucket `formatRelativeTime` picks, for hosts that word it themselves. */
export function relativeTimeParts(date: string | Date): { unit: RelativeTimeUnit; value: number } {
  const d = parseDate(date);
  const now = Date.now();
  const diff = now - d.getTime();
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const weeks = Math.floor(days / 7);
  const months = Math.floor(days / 30);

  if (seconds < 10) return { unit: "now", value: 0 };
  if (seconds < 60) return { unit: "s", value: seconds };
  if (minutes < 60) return { unit: "m", value: minutes };
  if (hours < 24) return { unit: "h", value: hours };
  if (days < 7) return { unit: "d", value: days };
  if (weeks < 5) return { unit: "w", value: weeks };
  if (months < 12) return { unit: "mo", value: months };
  return { unit: "y", value: Math.floor(months / 12) };
}

/** Wording per bucket, `{value}` standing for the count. Plain strings, so a
 *  server component can hand localised wording to a client one. */
export type RelativeTimeWords = Record<RelativeTimeUnit, string>;

export const RELATIVE_TIME_WORDS: RelativeTimeWords = {
  now: "just now",
  s: "{value}s ago",
  m: "{value}m ago",
  h: "{value}h ago",
  d: "{value}d ago",
  w: "{value}w ago",
  mo: "{value}mo ago",
  y: "{value}y ago",
};

/** Format a datetime to a relative string: "2h ago", "3d ago", "just now" */
export function formatRelativeTime(
  date: string | Date,
  words: Partial<RelativeTimeWords> = RELATIVE_TIME_WORDS,
): string {
  const { unit, value } = relativeTimeParts(date);
  return (words[unit] ?? RELATIVE_TIME_WORDS[unit]).replace("{value}", String(value));
}

/** Relative time that tolerates null/invalid input, returning `fallback`
 *  (default "—"). The single null-safe wrapper used by the owner surfaces. */
export function formatRelativeTimeOrNull(
  iso: string | null | undefined,
  fallback = "—",
): string {
  if (!iso) return fallback;
  const d = parseDate(iso);
  if (Number.isNaN(d.getTime()) || d.getTime() > Date.now()) return fallback;
  return formatRelativeTime(d);
}

/** What the absolute date formatters render for a missing or unparseable
 *  date, the same mark `formatRelativeTimeOrNull` uses. */
const NO_DATE = "—";

/** `parseDate` for a value that may be absent: null for a missing or
 *  unparseable one. `Intl.DateTimeFormat.format` throws a RangeError on an
 *  Invalid Date, so a single bad row would otherwise take down a whole table. */
export function parseDateOrNull(date: Timestamp | null | undefined): Date | null {
  if (date == null || date === "") return null;
  const d = parseDate(date);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Format a datetime to an absolute string: "Mar 19, 2026" (in UTC).
 *  A missing or unparseable date renders as "—". */
export function formatDate(date: Timestamp | null | undefined): string {
  const d = parseDateOrNull(date);
  if (d == null) return NO_DATE;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(d);
}

/** Format a datetime to full: "Mar 19, 2026 at 10:30 AM" (in UTC).
 *  A missing or unparseable date renders as "—". */
export function formatDateTime(date: Timestamp | null | undefined): string {
  const d = parseDateOrNull(date);
  if (d == null) return NO_DATE;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(d);
}

/** Format LOC counts: 50000 → "50K", 1234 → "1.2K" */
export function formatLOC(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/**
 * Truncate a file path keeping as many trailing components as fit:
 * src/very/long/path/file.py → …/long/path/file.py
 *
 * @deprecated Render paths with `<FilePath>` (`shared/file-path`), which clips
 * by the box width and keeps the full path in the tooltip and accessible name.
 * Kept for plain-string contexts and existing callers.
 */
export function truncatePath(path: string, maxChars = 60): string {
  if (path.length <= maxChars) return path;
  const parts = path.split("/");
  if (parts.length <= 1) return `…${path.slice(-(maxChars - 1))}`;
  // Progressively include more trailing path components until we exceed maxChars
  for (let i = parts.length - 2; i >= 1; i--) {
    const candidate = `…/${parts.slice(i).join("/")}`;
    if (candidate.length <= maxChars) return candidate;
  }
  // Just filename
  const filename = parts[parts.length - 1] ?? path;
  return filename.length <= maxChars ? `…/${filename}` : `…${filename.slice(-(maxChars - 1))}`;
}

/**
 * Shortest labels that still tell a set of paths apart: `src/css/mod.rs` and
 * `src/text/mod.rs` become `css/mod.rs` and `text/mod.rs`, while a path whose
 * basename is already unique keeps it. Each path grows by one trailing segment
 * per round until it is unique or has no segments left.
 *
 * Prefer `<FilePath>` for a path shown on its own; this is for many
 * paths displayed side by side, where a bare basename can be ambiguous.
 */
export function disambiguateBasenames(paths: Iterable<string>): Map<string, string> {
  const labels = new Map<string, string>();
  const unique = [...new Set(paths)];

  // Segments in use, grown only for the paths still tied at this depth.
  let depth = 1;
  let pending = unique;
  while (pending.length > 0) {
    const byLabel = new Map<string, string[]>();
    for (const path of pending) {
      const label = path.split("/").slice(-depth).join("/");
      const group = byLabel.get(label);
      if (group) group.push(path);
      else byLabel.set(label, [path]);
    }

    const stillTied: string[] = [];
    for (const [label, group] of byLabel) {
      // One path holds this label, or the group is already shown in full:
      // unreachable for a deduplicated set, but it bounds the loop.
      const exhausted = group.every((p) => p.split("/").length <= depth);
      if (group.length === 1 || exhausted) {
        for (const path of group) labels.set(path, label);
      } else {
        stillTied.push(...group);
      }
    }
    pending = stillTied;
    depth += 1;
  }

  return labels;
}

/** Format age in days to split units: 45 → "1 month 15 days", 400 → "1 year 1 month" */
export function formatAgeDays(n: number): string {
  if (n < 1) return "< 1 day";

  const days = Math.floor(n);
  const pluralize = (value: number, unit: string) =>
    `${value} ${unit}${value === 1 ? "" : "s"}`;

  if (days < 30) return pluralize(days, "day");
  if (days < 365) {
    const months = Math.floor(days / 30);
    const remainingDays = days % 30;
    return [pluralize(months, "month"), remainingDays && pluralize(remainingDays, "day")]
      .filter(Boolean)
      .join(" ");
  }

  let years = Math.floor(days / 365);
  let months = Math.floor((days % 365) / 30);
  if (months === 12) {
    years += 1;
    months = 0;
  }
  return [pluralize(years, "year"), months && pluralize(months, "month")]
    .filter(Boolean)
    .join(" ");
}

/** Strip inline markdown emphasis/code markers for plain-text display:
 *  "**`litellm` API-key resolution** in CLI" → "litellm API-key resolution in CLI" */
export function stripMarkdown(text: string): string {
  return text.replace(/\*\*|__|`/g, "");
}

/** Format a confidence score as a percentage string: 0.87 → "87%" */
export function formatConfidence(score: number): string {
  return `${Math.round(score * 100)}%`;
}

/** Format a job progress: 340 / 847 → "340 / 847 (40%)" */
export function formatProgress(done: number, total: number): string {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return `${formatNumber(done)} / ${formatNumber(total)} (${pct}%)`;
}

/** Format a ratio as a percentage: 0.873 → "87%", 1 → "100%" */
export function formatPercent(ratio: number, decimals = 0): string {
  if (!Number.isFinite(ratio)) return "—";
  const pct = ratio * 100;
  if (decimals <= 0) return `${Math.round(pct)}%`;
  return `${pct.toFixed(decimals)}%`;
}

/** Render a surfaced 0-100 percentile as an honest upper-tail rank. */
export function formatTopPercentile(percentile: number): string {
  const bounded = Math.min(Math.max(percentile, 0), 100);
  const upperTail = Math.round((100 - bounded) * 1e10) / 1e10;
  if (upperTail < 0.1) return "top <0.1%";
  if (upperTail < 10) return `top ${upperTail.toFixed(1).replace(/\.0$/, "")}%`;
  return `top ${Math.round(upperTail)}%`;
}

/** Format byte counts: 1536 → "1.5 KB", 1048576 → "1.0 MB" */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"] as const;
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  const formatted = value >= 10 || exponent === 0 ? value.toFixed(0) : value.toFixed(1);
  return `${formatted} ${units[exponent]}`;
}
