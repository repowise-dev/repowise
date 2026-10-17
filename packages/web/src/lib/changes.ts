/**
 * Pure Change Record helpers, safe to call from server and client components
 * alike. Kept out of the client table module: a server component cannot call
 * a function exported from a "use client" file.
 */
import type { ChangeListItem, EvidenceItem } from "@repowise-dev/types/changes";

/** The change's short name: the forge ref when it has one, else a trimmed id. */
export function changeLabel(c: Pick<ChangeListItem, "forge_ref" | "id">): string {
  return c.forge_ref ?? (c.id.length > 19 ? c.id.slice(0, 19) : c.id);
}

export type CommitsView = "changes" | "commits" | "events";

/**
 * The Commits page in one view: `?view=changes` groups by merged change,
 * `?view=events` lists what moved between index runs.
 */
export function commitsViewHref(repoId: string, view: CommitsView): string {
  const base = `/repos/${repoId}/commits`;
  return view === "commits" ? base : `${base}?view=${view}`;
}

/** The view a `?view=` value asks for; anything else is the commit view. */
export function parseCommitsView(raw: string | string[] | undefined): CommitsView {
  return raw === "changes" || raw === "events" ? raw : "commits";
}

/** Filters and paging of the by-change view, as its query parameters keep them. */
export interface ByChangeQuery {
  category?: string | undefined;
  attention?: boolean;
  offset?: number;
}

/** The by-change view with these filters; empty filters stay out of the URL. */
export function byChangeHref(repoId: string, next: ByChangeQuery = {}): string {
  const q = new URLSearchParams({ view: "changes" });
  if (next.category) q.set("category", next.category);
  if (next.attention) q.set("attention", "1");
  if (next.offset) q.set("offset", String(next.offset));
  return `${commitsViewHref(repoId, "commits")}?${q.toString()}`;
}

/** The commit categories a change can carry, in chip order. */
export const CHANGE_CATEGORIES = ["feature", "fix", "refactor", "docs", "test", "deps", "chore", "other"];

/** A category's name under `views.changes`; one this list does not know shows as stored. */
export function categoryLabel(t: (key: string) => string, category: string): string {
  return CHANGE_CATEGORIES.includes(category) ? t(`categories.${category}`) : category;
}

/** A `views.changes` translator that can also say whether it holds a key. */
export type ChangesTranslator = ((key: string, values?: Record<string, string | number>) => string) & {
  has: (key: string) => boolean;
};

/** An evidence item's value in one line, worded from the server's `line`; an unknown one says why. */
export function evidenceValueText(t: ChangesTranslator, item: EvidenceItem): string {
  const line = item.line;
  if (!line) return "";
  const params = line.params as Record<string, string | number>;
  if (line.key === "unknown" && !params.reason) return t("evidenceLine.unknownBare");
  const key = `evidenceLine.${line.key}`;
  // A key this build does not know (a newer server) shows nothing rather than a raw id.
  return t.has(key) ? t(key, params) : "";
}

/** An evidence label or basis from the catalog, falling back to the server's text. */
export function evidenceWord(t: ChangesTranslator, key: string, fallback: string): string {
  return t.has(key) ? t(key) : fallback;
}
