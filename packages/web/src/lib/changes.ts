/**
 * Pure Change Record helpers, safe to call from server and client components
 * alike. Kept out of the client table module: a server component cannot call
 * a function exported from a "use client" file.
 */
import type { ChangeListItem, ChangeRecord, EvidenceItem } from "@repowise-dev/types/changes";
import type { ChangePromptInput } from "@repowise-dev/ui/health/ai-prompt-builder";
import type { HealthDeltaLabels } from "@repowise-dev/ui/commits/health-delta-mark";

/** The change's short name: the forge ref when it has one, else a trimmed id. */
export function changeLabel(c: Pick<ChangeListItem, "forge_ref" | "id">): string {
  return c.forge_ref ?? (c.id.length > 19 ? c.id.slice(0, 19) : c.id);
}

export type CommitsView = "changes" | "commits" | "events" | "review";

/**
 * The Commits page in one view: `?view=changes` groups by merged change,
 * `?view=events` lists what moved between index runs, `?view=review` weighs
 * a change's blast radius before it lands.
 */
export function commitsViewHref(repoId: string, view: CommitsView): string {
  const base = `/repos/${repoId}/commits`;
  return view === "commits" ? base : `${base}?view=${view}`;
}

/** The view a `?view=` value asks for; anything else is the commit view. */
export function parseCommitsView(raw: string | string[] | undefined): CommitsView {
  return raw === "changes" || raw === "events" || raw === "review" ? raw : "commits";
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

/**
 * A checklist in the order the change page reads it. Health leads the page and
 * outcomes have their own section; provenance sits in the metadata line, so a
 * repository without attribution does not carry an "unknown" on every change.
 * What is left splits into exceptions (attention first, then unknown) and the
 * checks that passed.
 */
export interface EvidenceView {
  outcomes: EvidenceItem | undefined;
  exceptions: EvidenceItem[];
  passed: EvidenceItem[];
}

/**
 * Health leads from the change's own rollup and provenance sits in the
 * metadata line, so neither is listed (the server's attention count skips
 * health too, and provenance is never attention). Outcomes keep their own
 * section, but one needing attention (a revert) is also listed with the
 * exceptions, so the list's attention mark always has a row here.
 */
const NOT_LISTED = new Set(["health_delta", "provenance"]);

export function splitEvidence(items: EvidenceItem[]): EvidenceView {
  const rest = items.filter((i) => !NOT_LISTED.has(i.kind));
  return {
    outcomes: items.find((i) => i.kind === "outcomes"),
    exceptions: [
      ...rest.filter((i) => i.status === "attention"),
      ...rest.filter((i) => i.status === "unknown" && i.kind !== "outcomes"),
    ],
    passed: rest.filter((i) => i.status === "ok" && i.kind !== "outcomes"),
  };
}

/**
 * What the agent prompt needs from a change: the findings it made worse
 * (from its health rollup), or with none, the checklist rows needing a look.
 */
export function changePromptInput(
  t: ChangesTranslator,
  change: ChangeRecord,
  exceptions: EvidenceItem[] = [],
): ChangePromptInput {
  const health = change.health;
  return {
    label: changeLabel(change),
    title: change.title,
    head_sha: change.head_sha,
    commits: change.commits,
    files: change.files.map((f) => f.path),
    findings: health?.findings ?? [],
    omitted: health ? Math.max(0, health.introduced + health.worsened - (health.findings?.length ?? 0)) : 0,
    resolved: health?.resolved ?? 0,
    complete: health?.status !== "partial",
    attention: exceptions.map(
      (i) => `${evidenceWord(t, `evidenceKinds.${i.kind}`, i.label)}: ${evidenceValueText(t, i) || t(`status${i.status === "attention" ? "Attention" : "Unknown"}`)}`,
    ),
  };
}

/** The same prompt narrowed to one file: its findings, and only that file. */
export function filePromptInput(input: ChangePromptInput, path: string): ChangePromptInput {
  // Resolved and omitted counts are change-wide; a per-file prompt does not claim them.
  return { ...input, files: [path], findings: input.findings.filter((f) => f.path === path), resolved: 0, omitted: 0, attention: [] };
}

/** The health mark's words, from the catalog; one place for the list and the lede. */
export function healthDeltaLabels(t: (key: string) => string): HealthDeltaLabels {
  return {
    improved: t("healthLabels.improved"),
    worsened: t("healthLabels.worsened"),
    mixed: t("healthLabels.mixed"),
    neutral: t("healthLabels.neutral"),
    notCompared: t("healthLabels.notCompared"),
    partial: t("healthLabels.partial"),
    stale: t("healthLabels.stale"),
  };
}
