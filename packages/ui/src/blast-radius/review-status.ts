/**
 * The one-line verdict a change review leads with: a word, and the fact that
 * earned it. Pure, so the screen and the review prompt say the same thing.
 *
 * It never claims a change is safe; it says what to do first. "Needs tests"
 * when a changed file has no test, "Look first" when a changed file changes
 * often or has weak health, many files depend on the change, or files that
 * usually move with it were left out; otherwise "Small change" or
 * "Contained", with the size of it.
 */

import { isWeak, type Reach } from "./blast-map-model";

export type ReviewTone = "calm" | "look";

export interface ReviewStatus {
  tone: ReviewTone;
  word: string;
  reason: string;
}

/** Above this many dependent files a change reads as broad, whatever its band. */
const WIDE_REACH = 200;
const SMALL_FILES = 3;
const SMALL_REACH = 25;

/** `n word` with an `s` past one, the number grouped. */
export function count(n: number, word: string, plural = `${word}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? word : plural}`;
}

/** A figure above the fold: exact up to a thousand, then "2,000+", never more digits than meaning. */
export function roughCount(n: number): string {
  return n < 1000 ? n.toLocaleString() : `${(Math.floor(n / 1000) * 1000).toLocaleString()}+`;
}

export interface ReviewFacts {
  reach: Reach;
  /** Every path the change touches, analyzed or not. */
  total: number;
  testsTotal: number;
  /** Distinct files that usually change with these and are left out. */
  missingPartners: number;
  band: "localized" | "moderate" | "broad";
}

function lookReason(f: ReviewFacts): string | null {
  const risky = f.reach.changed.filter((m) => m.isHotspot || isWeak(m)).length;
  if (risky > 0) return `${count(risky, "changed file")} ${risky === 1 ? "changes" : "change"} often or ${risky === 1 ? "has" : "have"} weak health.`;
  if (f.band === "broad" || f.reach.files.length > WIDE_REACH)
    return `${roughCount(f.reach.files.length)} source ${f.reach.files.length === 1 ? "file depends" : "files depend"} on it.`;
  if (f.missingPartners > 0)
    return `${count(f.missingPartners, "file")} that usually ${f.missingPartners === 1 ? "changes" : "change"} with these ${f.missingPartners === 1 ? "is" : "are"} not in it.`;
  return null;
}

export function reviewStatus(f: ReviewFacts): ReviewStatus {
  const untested = f.reach.changed.filter((m) => m.untested).length;
  if (untested > 0)
    return { tone: "look", word: "Needs tests", reason: `${count(untested, "changed file")} with no test found.` };
  const look = lookReason(f);
  if (look) return { tone: "look", word: "Look first", reason: look };
  const small = f.total <= SMALL_FILES && f.reach.files.length <= SMALL_REACH;
  const tests = f.testsTotal > 0 ? `${count(f.testsTotal, "test")} to run` : "no test found to run";
  return {
    tone: "calm",
    word: small ? "Small change" : "Contained",
    reason: `${count(f.total, "file")} changed, ${tests}.`,
  };
}
