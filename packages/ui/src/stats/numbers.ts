/**
 * Number wording for the Stats page, kept to the precision the data can bear.
 *
 * Whole percentages only, large counts shortened with the exact figure kept
 * for `title`/`aria-label`, and momentum that refuses to put a percentage on
 * a handful of commits.
 */

import type { StatsVelocity } from "@repowise-dev/types/stats";
import { formatNumber } from "../lib/format";

/** Full below 100k, short above: 13,377 but 504k and 1.26M. */
export function shortCount(n: number): string {
  if (n < 100_000) return formatNumber(Math.round(n));
  if (n < 999_500) return `${Math.round(n / 1_000)}k`;
  return `${Number((n / 1_000_000).toFixed(2))}M`;
}

/** Whole percent of `part` in `total`, 0 for an empty total. */
export function wholePct(part: number, total: number): number {
  return total > 0 ? Math.round((part / total) * 100) : 0;
}

/** Below this on either side, a percentage change is noise, so counts are stated. */
export const MOMENTUM_MIN_COMMITS = 50;

export interface Momentum {
  /** The figure: "+174%", "Steady", or a plain commit count. */
  value: string;
  unit?: string;
  /** The sentence that says what was compared. */
  sub: string;
}

/**
 * The last 90 days against the 90 before.
 *
 * Under `MOMENTUM_MIN_COMMITS` on either side the counts are stated ("16
 * commits, up from 7") rather than turned into "+129%". When the prior window
 * reaches past the indexed sample (`pct_change` null) there is nothing honest
 * to compare against, so only the recent count shows.
 */
export function momentum(v: StatsVelocity | null | undefined): Momentum | null {
  if (!v) return null;
  const { recent_90d: recent, prior_90d: prior } = v;
  if (v.pct_change == null) {
    return { value: formatNumber(recent), unit: "commits", sub: "in the last 90 days" };
  }
  if (recent < MOMENTUM_MIN_COMMITS || prior < MOMENTUM_MIN_COMMITS) {
    return { value: formatNumber(recent), unit: "commits", sub: `${countTrend(recent, prior)} the 90 days before` };
  }
  const pct = Math.round(((recent - prior) / prior) * 100);
  const sub = `${formatNumber(recent)} commits against ${formatNumber(prior)} the 90 days before`;
  if (Math.abs(pct) < 10) return { value: "Steady", sub };
  return { value: `${pct > 0 ? "+" : ""}${pct}%`, sub };
}

/** "up from 7", "down from 40", "the same as 12". */
function countTrend(recent: number, prior: number): string {
  if (recent === prior) return `the same as ${formatNumber(prior)}`;
  return `${recent > prior ? "up" : "down"} from ${formatNumber(prior)}`;
}

/** A gap in hours: "Under an hour", whole hours under two days, whole days above. */
export function gapLabel(hours: number): { value: string; unit: string } {
  if (hours < 1) return { value: "Under", unit: "an hour" };
  if (hours < 48) {
    const whole = Math.round(hours);
    return { value: formatNumber(whole), unit: whole === 1 ? "hour" : "hours" };
  }
  const days = Math.round(hours / 24);
  return { value: formatNumber(days), unit: "days" };
}

/** An age in days, as days under a year and whole years above. */
export function ageLabel(days: number): { value: string; unit: string } {
  if (days < 365) return { value: formatNumber(days), unit: days === 1 ? "day" : "days" };
  const years = Math.max(1, Math.floor(days / 365.25));
  return { value: formatNumber(years), unit: years === 1 ? "year" : "years" };
}

/** 0 -> "12am", 13 -> "1pm". */
export function hourLabel(h: number): string {
  return `${h % 12 === 0 ? 12 : h % 12}${h < 12 ? "am" : "pm"}`;
}
