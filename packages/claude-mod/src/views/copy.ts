/**
 * Every string Lens shows. Commands are written out whole so they can be
 * copied; Lens never runs them.
 */

import { countOf } from "../format";
import type { FileContext, HintKind, IndexFreshness, MarginNote, SavingsDelta } from "../model/session";
import type { Squeeze } from "../model/squeeze";

export const HINTS: Record<HintKind, string> = {
  "no-server": "Lens map needs the local server: repowise serve --no-ui",
  auth: "local server needs an API key; Lens does not read keys",
  unlisted: "Lens map needs a local server for this repo: repowise serve --no-ui",
  "no-index": "index this repo for Lens: repowise init --no-prose --yes",
  "no-cli": "Lens needs the Repowise CLI: pip install repowise",
};

export function freshnessLine(f: IndexFreshness): string {
  const changed = f.changedFiles === null ? "" : ` (${countOf(f.changedFiles, "file", "files")} changed)`;
  return `index behind HEAD${changed} · repowise update`;
}

/** `query.py · 41 caller files · 3 contributors`; unknown or zero counts are left out. */
export function spinnerLine(file: string, ctx: FileContext): string {
  const parts = [file.slice(file.lastIndexOf("/") + 1)];
  if (ctx.callerFiles) parts.push(countOf(ctx.callerFiles, "caller file", "caller files"));
  if (ctx.contributors) parts.push(countOf(ctx.contributors, "contributor", "contributors"));
  return parts.join(" · ");
}

/** `1,204 lines → 38 · ~9,400 tokens omitted · 4 failed · repowise expand 9c1e4b2a7f01`. */
export function squeezeLine(s: Squeeze): string {
  const parts = [`${countOf(s.originalLines, "line", "lines")} → ${s.keptLines.toLocaleString("en-US")}`];
  if (s.omittedTokens > 0) parts.push(`~${countOf(s.omittedTokens, "token", "tokens")} omitted`);
  // As the runner wrote it: pytest's `4 failed, 1 error`.
  const failures = [s.failed > 0 ? `${s.failed.toLocaleString("en-US")} failed` : "", s.errors > 0 ? countOf(s.errors, "error", "errors") : ""];
  if (s.failed + s.errors > 0) parts.push(failures.filter((f) => f !== "").join(", "));
  const more = s.refs.length > 1 ? ` (+${s.refs.length - 1} more)` : "";
  parts.push(`repowise expand ${s.refs[0]}${more}`);
  return parts.join(" · ");
}

/**
 * A margin note as help, never a label, saying only what the hook emitted.
 * The age is always said, so a run of fixes last week never reads like one
 * from last spring.
 */
export function marginLine(note: MarginNote): string {
  if (note.kind === "decision") {
    return note.reviewed
      ? `a standing decision covers this file: ${note.title}`
      : `a decision found in this file, not yet reviewed: ${note.title}`;
  }
  const where = note.symbol === null ? "" : `, mostly in ${note.symbol}`;
  return `fixed ${countOf(note.count, "time", "times")} in 6 months, most recently ${note.age}${where}`;
}

/**
 * `182,400 tokens · $2.41 saved since this session started · all agents on this repo`.
 * The scope stays on screen: a narrow band gets the short form, not a cut one.
 */
export function savingsLine(d: SavingsDelta, columns: number): string {
  const inferred = d.inferredTokens > 0 ? ` (${d.inferredTokens.toLocaleString("en-US")} inferred)` : "";
  // Input-side dollars, like the token count. Under half a cent rounds to
  // $0.00, which would read as nothing priced.
  const usd = d.usd >= 0.005 ? ` · $${d.usd.toFixed(2)}` : "";
  const head = `${countOf(d.tokens, "token", "tokens")}${inferred}${usd} saved`;
  const full = `${head} since this session started · all agents on this repo`;
  return full.length <= columns ? full : `${head} this session · all agents`;
}

// Change review. Figures carry their unit; "measured" and "inferred" are said in words.

export const REVIEWING = "Reviewing the change...";
export const REVIEW_TIMEOUT_S = 20;
export const REVIEW_TIMED_OUT = `Change review timed out after ${REVIEW_TIMEOUT_S} s`;
export const RUN_TESTS = "Run tests";
export const DETAILS = "Details";
/** Branch overlap is a fact, not a health band: a neutral mark plus words. */
export const OVERLAP_MARK = "◦";

export function reviewFailed(message: string): string {
  return `Change review could not run: ${message}`;
}

/** `working tree, 2 changed files`, or the commit a clean tree fell back to. */
export function reviewScope(ref: string, workingTree: boolean, changed: number | null): string {
  const where = workingTree ? ref : `${ref} (no uncommitted changes)`;
  return changed === null ? where : `${where}, ${countOf(changed, "changed file", "changed files")}`;
}

export function noNewFindings(analyzed: number): string {
  return `no new findings in ${analyzed === 1 ? "the 1 changed file" : `all ${analyzed.toLocaleString("en-US")} changed files`}`;
}

export function healthImproved(resolved: number): string {
  return `improved, ${countOf(resolved, "finding", "findings")} resolved, none new`;
}

export const HEALTH_NOT_REPORTED = "not reported";
export const NO_NEW_FINDINGS = "no new findings";
export const PARTLY_COMPARED = "partly compared, no new findings";

export function notCompared(why: string): string {
  return why === "" ? "not compared" : `not compared: ${why}`;
}

export function improvedShort(resolved: number): string {
  return `improved, ${resolved.toLocaleString("en-US")} resolved`;
}

/** `2 new findings, review required`; low and advisory severities are worth a look, not a gate. */
export function newFindings(total: number, required: boolean): string {
  return `${countOf(total, "new finding", "new findings")}, ${required ? "review required" : "low severity"}`;
}

export function resolvedToo(resolved: number): string {
  return `${countOf(resolved, "finding", "findings")} resolved`;
}

export function findingLine(f: { severity: string; biomarker: string; reason: string; path: string; lines?: [number, number] }): string {
  const at = f.lines === undefined ? f.path : `${f.path}:${f.lines[0]}`;
  return `${f.severity} ${f.biomarker}: ${f.reason} (${at})`;
}

export function moreFindings(more: number): string {
  return `and ${more.toLocaleString("en-US")} more (Details)`;
}

export function partialScope(analyzed: number, changed: number, reasons: Record<string, number>): string {
  const why = Object.entries(reasons)
    .map(([reason, n]) => `${n.toLocaleString("en-US")} ${reason.replace(/_/g, " ")}`)
    .join(", ");
  const skipped = changed - analyzed;
  return (
    `Scope: compared ${analyzed.toLocaleString("en-US")} of ${countOf(changed, "changed file", "changed files")}; ` +
    `${skipped.toLocaleString("en-US")} not analysed${why ? ` (${why})` : ""}, so this is not a clean bill`
  );
}

export function diffShape(percentile: number | null | undefined): string {
  const where =
    percentile === null || percentile === undefined
      ? "not ranked (no recent commits to compare)"
      : `bigger than ${Math.round(percentile)}% of this repo's recent commits`;
  return `Diff shape: ${where}; size, not danger`;
}

export function testsBasis(measured: boolean): string {
  return measured ? "measured by stored coverage" : "inferred from the dependency graph, not measured";
}

export function testsLine(t: { tests: string[]; total: number; truncated: boolean; files: boolean; measured: boolean }): string {
  const count = countOf(t.total, t.files ? "test file" : "test", t.files ? "test files" : "tests");
  const shown = t.truncated ? ` (first ${t.tests.length.toLocaleString("en-US")} shown)` : "";
  return `Tests to run: ${count}, ${testsBasis(t.measured)}${shown}: ${t.tests.join(", ")}`;
}

export function overlapLine(branches: string[], files: string[], more: boolean): string {
  const who = countOf(branches.length, "other branch", "other branches") + (more ? " or more" : "");
  const what = files.length === 1 ? files[0] : countOf(files.length, "changed file", "changed files");
  const verb = branches.length === 1 && !more ? "edits" : "edit";
  return `${OVERLAP_MARK} ${who} also ${verb} ${what} (${branches.join(", ")})`;
}

export function overlapShort(branches: number): string {
  return `${OVERLAP_MARK} ${countOf(branches, "other branch edits", "other branches edit")} these files`;
}

export function directiveLines(d: { status: string; headline: string; reasons?: string[]; next_actions?: string[] }): string[] {
  const out = [`Change review: ${d.status.replace(/_/g, " ")}`, d.headline];
  if (d.reasons?.length) out.push("Reasons:", ...d.reasons.map((r) => `  ${r}`));
  if (d.next_actions?.length) out.push("Next actions:", ...d.next_actions.map((a) => `  ${a}`));
  return out;
}

/** The prompt `Run tests` submits, visible in the transcript. */
export function runTestsPrompt(t: { tests: string[]; truncated: boolean; total: number; measured: boolean }): string {
  const rest = t.truncated ? ` (the first ${t.tests.length} of ${t.total})` : "";
  return `Run the tests Repowise names for this change${rest}, ${testsBasis(t.measured)}: ${t.tests.join(" ")}`;
}
