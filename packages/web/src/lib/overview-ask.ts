import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";

/** A translator for the `overview.ask` keys. */
export type AskTranslator = (key: string, values?: Record<string, string>) => string;

const MAX_SUGGESTIONS = 4;
const MIN_SUGGESTIONS = 3;
const TITLE_CHARS = 60;

function clip(text: string): string {
  return text.length > TITLE_CHARS ? `${text.slice(0, TITLE_CHARS - 1).trimEnd()}…` : text;
}

/**
 * Questions for the ask box built from this repo's own summary: its busiest
 * hotspot, its lowest-scoring file, its newest decision and its health trend.
 * Topped up with the generic openers when the index has too little to ask
 * about.
 */
export function overviewAskSuggestions(
  summary: OverviewSummaryResponse,
  t: AskTranslator,
): string[] {
  const out: string[] = [];
  const hotspot = summary.top_hotspots[0]?.file_path;
  if (hotspot) out.push(t("ask.hotspotOwners", { path: hotspot }));
  const worst = summary.health.worst_performer_path;
  if (worst && worst !== hotspot) out.push(t("ask.worstFile", { path: worst }));
  const decision = summary.recent_decisions[0]?.title;
  if (decision) out.push(t("ask.decision", { title: clip(decision) }));
  if (summary.health.history.length >= 2) out.push(t("ask.trend"));
  for (const key of ["ask.suggestion1", "ask.suggestion2", "ask.suggestion3"]) {
    if (out.length >= MIN_SUGGESTIONS) break;
    out.push(t(key));
  }
  return out.slice(0, MAX_SUGGESTIONS);
}
