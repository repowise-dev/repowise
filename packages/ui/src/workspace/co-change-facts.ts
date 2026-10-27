/**
 * The facts and wording the co-change page, its drawer and its agent prompts
 * share, so a definition or a sentence has one source. No "use client": the
 * server page reads the definitions too.
 */

import type { WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";

/** The miner's session window: an author's commits chain into one session while under this gap. */
export const SESSION_WINDOW_HOURS = 24;

/** Reconstructed commit pairs shown in the drawer and listed in the prompt. */
export const MAX_COMMIT_PAIRS = 8;

export const STRENGTH_DEFINITION =
  "Share of the less active file's recent work sessions that also touched its partner, weighted toward recent sessions. 100% would mean they always change together. A work pattern from git history, not a verified dependency.";

export const SESSIONS_DEFINITION = `Work sessions in which both files changed: one author's commits, chained while they stay within ${SESSION_WINDOW_HOURS} hours of each other, touching both repositories.`;

/** Order-independent id of a repository pair, e.g. `backend↔frontend`. */
export function repoPairId(a: string, b: string): string {
  const [x, y] = [a, b].sort();
  return `${x}↔${y}`;
}

export function coChangeRepoPairId(cc: WorkspaceCoChangeEntry): string {
  return repoPairId(cc.source_repo, cc.target_repo);
}

export function gapPhrase(hours: number): string {
  return hours < 1 ? "under an hour apart" : `${Math.round(hours)}h apart`;
}

export function byTypePhrase(byType: Record<string, number>): string {
  return Object.entries(byType)
    .sort((a, b) => b[1] - a[1])
    .map(([t, n]) => `${n} ${t}`)
    .join(", ");
}

/** One contract link joining the two files, as the structure endpoint returns it. */
export interface CoChangeLink {
  contract_id: string;
  contract_type: string;
  provider_repo: string;
  provider_file: string;
  consumer_repo: string;
  consumer_file: string;
}

/** Declared structure behind one file pair. */
export interface CoChangeStructureFacts {
  pairLinks: CoChangeLink[];
  repoLinksTotal: number;
  repoLinksByType: Record<string, number>;
}

/**
 * The hidden-coupling finding in prose, for a pair no contract joins.
 * `name` formats a repository name (the prompt wraps it in backticks).
 */
export function hiddenCouplingSentence(
  pair: WorkspaceCoChangeEntry,
  s: CoChangeStructureFacts,
  name: (repo: string) => string = (r) => r,
): string {
  const a = name(pair.source_repo);
  const b = name(pair.target_repo);
  const repoPart =
    s.repoLinksTotal > 0
      ? `Between ${a} and ${b} there are ${s.repoLinksTotal} contract links (${byTypePhrase(s.repoLinksByType)}), none through these two files.`
      : `No contract links ${a} and ${b} at all.`;
  return `No contract repowise extracted connects these two files, so nothing declared explains why they move together: a hidden coupling. ${repoPart}`;
}

// ---------------------------------------------------------------------------
// Commit evidence
// ---------------------------------------------------------------------------

export interface CoChangeCommit {
  sha: string;
  /** ISO timestamp. */
  date: string;
  message?: string | null;
  author?: string | null;
}

export interface NearbyCommitPair {
  source: CoChangeCommit;
  target: CoChangeCommit;
  /** Absolute gap between the two commits, in hours. */
  gapHours: number;
}

/**
 * Pair each source commit with the closest target commit by the same author
 * inside the session window, newest first.
 *
 * A reconstruction, not the miner's record: the miner keeps a session count
 * and a date, never the commits, and the inputs here are each file's recent
 * significant commits, so a real shared session can be missing from the result.
 */
export function nearbyCommits(
  source: CoChangeCommit[],
  target: CoChangeCommit[],
  windowHours: number = SESSION_WINDOW_HOURS,
): NearbyCommitPair[] {
  const same = (a?: string | null, b?: string | null) =>
    !!a && !!b && a.trim().toLowerCase() === b.trim().toLowerCase();
  const out: NearbyCommitPair[] = [];
  for (const s of source) {
    const st = Date.parse(s.date);
    if (Number.isNaN(st)) continue;
    let best: NearbyCommitPair | null = null;
    for (const t of target) {
      if (!same(s.author, t.author)) continue;
      const tt = Date.parse(t.date);
      if (Number.isNaN(tt)) continue;
      const gapHours = Math.abs(st - tt) / 3_600_000;
      if (gapHours > windowHours) continue;
      if (!best || gapHours < best.gapHours) best = { source: s, target: t, gapHours };
    }
    if (best) out.push(best);
  }
  return out.sort((a, b) => Date.parse(b.source.date) - Date.parse(a.source.date));
}

// ---------------------------------------------------------------------------
// Scope
// ---------------------------------------------------------------------------

/** Which of the miner's caps trimmed the stored pairs, as the server reports it. */
export interface CoChangeCaps {
  truncatedBy: "total" | "per_repo_pair" | null;
  perRepoPairCap: number | null;
  totalCap: number | null;
}

/** The rule that trimmed the list, as a clause; null when nothing was dropped. */
export function capRule(c: CoChangeCaps): string | null {
  if (c.truncatedBy === "per_repo_pair" && c.perRepoPairCap) {
    return `the miner keeps the strongest ${c.perRepoPairCap} for each repository pair`;
  }
  if (c.truncatedBy === "total" && c.totalCap) {
    return `the miner keeps the strongest ${c.totalCap} across the workspace${
      c.perRepoPairCap ? `, and at most ${c.perRepoPairCap} for any one repository pair` : ""
    }`;
  }
  return c.truncatedBy ? "the miner keeps only the strongest" : null;
}
