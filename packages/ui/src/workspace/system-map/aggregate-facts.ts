/**
 * The words and figures an aggregate carries: where a group came from, how
 * healthy its services are, what a band's links are made of. Shared by the
 * cards, the bands, their accessible names and the rail, so a figure reads
 * the same everywhere it appears. Pure, no React.
 */

import { HEALTH_MARK_BANDS, healthMark, type HealthMarkBand } from "../../health/tokens";
import { EDGE_KIND_ORDER, edgeKindStyle } from "./edge-kinds";
import type { LinkSummary } from "./level-types";
import { plural } from "./system-map-model";

/**
 * Where a group came from, as a reader can check it: `tag: storefront`,
 * `name: ordering.*`, `linked: 6 services`. Null for an unknown source, so
 * nothing is invented.
 */
export function groupSourceLabel(groupId: string, source: string | null | undefined, members: number): string | null {
  const name = groupId.includes(":") ? groupId.slice(groupId.indexOf(":") + 1) : groupId;
  if (source === "tag") return `tag: ${name}`;
  if (source === "name") return `name: ${name}.*`;
  if (source === "linked") return `linked: ${plural(members, "service", "services")}`;
  return null;
}

export type HealthCounts = Record<HealthMarkBand, number> & { unscored: number };

/** Services per canonical health band; `score10` is null for a service with no score. */
export function healthCounts(scores: Iterable<number | null>): HealthCounts {
  const out: HealthCounts = { healthy: 0, warning: 0, alert: 0, unscored: 0 };
  for (const s of scores) {
    if (s == null) out.unscored += 1;
    else out[healthMark(s).band] += 1;
  }
  return out;
}

export interface HealthException {
  band: HealthMarkBand;
  count: number;
  label: string;
  color: string;
}

/** The bands worth marking on a group: Alert, then Warning. Healthy is the quiet default. */
export function healthExceptions(counts: HealthCounts): HealthException[] {
  return HEALTH_MARK_BANDS.filter((b) => b.band !== "healthy" && counts[b.band] > 0)
    .map((b) => ({ band: b.band, count: counts[b.band], label: b.label, color: b.color }))
    .reverse();
}

/** "All healthy", "1 Alert, 2 Warning", or "No health scores" when none of the services has one. */
export function healthSummaryText(counts: HealthCounts): string {
  const exceptions = healthExceptions(counts);
  if (exceptions.length > 0) return exceptions.map((e) => `${e.count} ${e.label}`).join(", ");
  if (counts.healthy > 0) return counts.unscored > 0 ? `${counts.healthy} Healthy, ${counts.unscored} unscored` : "All healthy";
  return "No health scores";
}

/** "HTTP 17, Event 6": links per kind, in the legend's kind order. */
export function kindMixText(kinds: Readonly<Record<string, number>>): string {
  const known = EDGE_KIND_ORDER.filter((k) => (kinds[k] ?? 0) > 0);
  const unknown = Object.keys(kinds).filter((k) => !known.includes(k as never) && (kinds[k] ?? 0) > 0).sort();
  return [...known, ...unknown].map((k) => `${edgeKindStyle(k).label} ${kinds[k]}`).join(", ");
}

/**
 * A band in one sentence: "storefront depends on catalog: 17 links. HTTP 17,
 * 2 of them through a gateway." The arrow runs consumer to provider, so the
 * verb is "depends on" whatever the kinds are.
 */
export function bandSentence(source: string, target: string, summary: LinkSummary): string {
  const head = `${source} depends on ${target}: ${plural(summary.links, "link", "links")}.`;
  const mix = kindMixText(summary.kinds);
  const gateway =
    summary.gatewayLinks > 0
      ? `, ${summary.gatewayLinks === summary.links ? "all" : summary.gatewayLinks} of them through a gateway`
      : "";
  return mix ? `${head} ${mix}${gateway}.` : head;
}
