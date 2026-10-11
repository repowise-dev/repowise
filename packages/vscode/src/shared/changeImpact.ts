/**
 * Pure change-impact helpers, free of `vscode` and the api-client so they can be
 * shared by the extension host (analysis service, nudge feature) and unit-tested
 * in the webview vitest environment without a VS Code host. Runtime-dependency
 * free by design; only a type import from the message contract.
 */

import type { ChangeImpactReport } from "./webviewMessages";

/** Stable signature of a change set; identical sets share a cache entry. */
export function changeSignature(paths: string[]): string {
  return paths.join("\n");
}

/** A file not in the change set that historically changes with a changed file. */
export interface MissingCochange {
  /** Repo-relative path the user has not touched. */
  partner: string;
  /** Strongest co-change count among the changed files pointing at it. */
  score: number;
  /** An example changed file it partners with (for the "why"). */
  withChanged: string;
  /** How many changed files point at this partner. */
  count: number;
}

/**
 * Reduces the blast response's per-pair co-change warnings to one ranked entry
 * per missing partner, dropping anything below `minScore` or already in the
 * change set. Ranked by strength, strongest first.
 */
export function selectMissingCochanges(
  report: ChangeImpactReport,
  minScore: number,
): MissingCochange[] {
  const changed = new Set(report.changed);
  const byPartner = new Map<string, MissingCochange>();
  for (const w of report.blast?.cochange_warnings ?? []) {
    if (w.score < minScore || changed.has(w.missing_partner)) continue;
    const existing = byPartner.get(w.missing_partner);
    if (existing) {
      existing.count += 1;
      if (w.score > existing.score) {
        existing.score = w.score;
        existing.withChanged = w.changed;
      }
    } else {
      byPartner.set(w.missing_partner, {
        partner: w.missing_partner,
        score: w.score,
        withChanged: w.changed,
        count: 1,
      });
    }
  }
  return [...byPartner.values()].sort((a, b) => b.score - a.score);
}
