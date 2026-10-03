/**
 * Every string Lens shows. Commands are written out whole so they can be
 * copied; Lens never runs them.
 */

import { formatNumber } from "@repowise-dev/ui/lib/format";
import { countOf, fit } from "../format";
import type { FileContext, HintKind, IndexFreshness, MarginNote, SavingsDelta } from "../model/session";
import type { Squeeze } from "../model/squeeze";
import type { Callers } from "../model/trail";

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
export const WHY = "Why";
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

// The living map.

export const MAP_COPY = {
  title: "Lens",
  looking: "Lens map: looking for the local server",
  loading: "Lens map: loading the health map",
  failed: "Lens map could not load the health map; it tries again on /lens",
  desktop: "Lens map: not drawn on this surface (terminal and desktop app only)",
  tooLarge: "Lens map has too much detail to draw here; the terminal map shows it",
  noScore: "Not scored",
  read: "Claude read",
  edited: "edited",
  importer: "imports the edited file",
  match: "search match",
  waiting: "Lens map is ready; this pane is too narrow to open on its own. Run /lens",
} as const;

/** What the desktop map says to a reader that cannot see it. */
export function mapAlt(drawn: number): string {
  return `Code health map: ${countOf(drawn, "file", "files")} drawn, colored by health band, with the files Claude read and edited marked`;
}

/** /lens could not place the pane; the engine's reason names the width it needs. */
export function notPlacedLine(reason: string): string {
  return `Lens map needs a wider terminal: ${reason}`;
}

export interface ScopeFacts {
  drawn: number;
  repositoryTotal: number;
  /** "2h ago", or null when the server did not say. */
  indexed: string | null;
  /** Files the server left out at its cap, and the cap. */
  beyondCap: number;
  cap: number;
}

/** Scope parts a narrow legend may leave out; the counts and the cap always stay. */
export const SCOPE_REST = "rest empty or too small";
export function indexedPart(age: string): string {
  return `indexed ${age}`;
}

/** The scope, as parts the legend joins with ` · ` and wraps onto two rows when narrow. */
export function scopeParts(s: ScopeFacts): string[] {
  const parts =
    s.drawn === s.repositoryTotal
      ? [`${countOf(s.drawn, "file", "files")}, all drawn`]
      : [`${formatNumber(s.drawn)} of ${countOf(s.repositoryTotal, "file", "files")} drawn at this size`, SCOPE_REST];
  if (s.indexed !== null) parts.push(indexedPart(s.indexed));
  if (s.beyondCap > 0) parts.push(`${formatNumber(s.beyondCap)} beyond the ${formatNumber(s.cap)}-file cap`);
  return parts;
}

/**
 * Reads so far (`200+` once capped), what was not drawn, and what the last
 * search matched. Narrower than `columns`, a short form that keeps every count.
 */
export function readsLine(
  reads: { count: number; capped: boolean; notDrawn: number },
  matched: number | null,
  columns = Number.POSITIVE_INFINITY,
): string | null {
  const count = reads.capped ? `${formatNumber(reads.count)}+` : formatNumber(reads.count);
  const full: string[] = [];
  const short: string[] = [];
  if (reads.count > 0) {
    full.push(reads.capped ? `${count} files read` : countOf(reads.count, "file read", "files read"));
    short.push(`${count} read`);
  }
  if (reads.notDrawn > 0) {
    full.push(`${formatNumber(reads.notDrawn)} not drawn`);
    short.push(`${formatNumber(reads.notDrawn)} not drawn`);
  }
  if (matched !== null) {
    full.push(`last search matched ${countOf(matched, "file", "files")}`);
    short.push(`search matched ${formatNumber(matched)}`);
  }
  if (full.length === 0) return null;
  const line = full.join(" · ");
  return line.length <= columns ? line : short.join(" · ");
}

/**
 * The importers of the edited file: inferred from the import graph, not
 * observed calls. Narrower than `columns`, a short form that keeps the counts
 * and cuts the file name instead (the legend's swatch names the basis).
 */
export function callersLine(name: string, callers: Callers | null, notDrawn: number, columns = Number.POSITIVE_INFINITY): string {
  if (callers === null || callers.status === "loading") return `edited ${name} · finding the files that import it`;
  if (callers.status === "failed") return `edited ${name} · import graph unavailable`;
  const missing = notDrawn === 0 ? "" : ` · ${formatNumber(notDrawn)} not drawn`;
  const full = `edited ${name} · ${countOf(callers.paths.length, "file imports", "files import")} it (from imports, not calls)${missing}`;
  if (full.length <= columns) return full;
  const rest = ` · ${formatNumber(callers.paths.length)} import it${missing}`;
  return `edited ${fit(name, columns - "edited ".length - rest.length)}${rest}`;
}

// The pane's tabs, Ask and Recap.

export const PANE_COPY = {
  command: "Open Lens: the health map, ask the index, and the session recap",
  argumentHint: "[map | ask <question> | recap]",
  tabs: { map: "Map", ask: "Ask", recap: "Recap" },
  askLabel: "ask ",
  askPlaceholder: "why ...? asks the decision records; anything else asks the index",
  askSubmit: "ask",
  askIdle: "Ask about this repo. Answers cite the evidence they used.",
  askBusy: "still answering the last question; ask again when it lands",
  noEvidence: "no evidence cited",
  fromIndex: "Built from the index",
  fromModel: "Written by this repo's configured model, from the index",
} as const;

/** Characters of a reply the Ask tab draws; the Markdown element takes at most 10,000. */
export const ASK_CHARS = 4_000;

export function askingLine(tool: "get_why" | "get_answer"): string {
  return tool === "get_why" ? "asking the decision records..." : "asking the index...";
}

export function askFailedLine(message: string): string {
  return `Could not answer: ${message}`;
}

export function askCut(chars: number): string {
  return `reply cut at ${formatNumber(chars)} characters`;
}

export function moreRows(more: number): string {
  return `and ${formatNumber(more)} more`;
}

export const RECAP_COPY = {
  files: "Files",
  fromReview: "from the last change review",
  review: "Change review",
  health: "Code health",
  findings: "Findings",
  tests: "Tests to run",
  overlap: "Branches overlapping",
  saved: "Saved",
  decisions: "Decisions surfaced",
  noReview: "none this session",
  notCompared: "not compared",
  noTests: "none named",
  noOverlap: "none found",
  notReported: "not reported",
  noSavings: "nothing yet since this session started",
  savingsNeedServer: "needs the local server: repowise serve --no-ui",
  noDecisions: "none",
  noModel: "Lens made no model calls.",
  fromIndex: "Every figure here is read from the local index.",
} as const;

/** `4 edited of 41 files touched`, `200+` once the trail is capped. */
export function filesTouched(edited: number, touched: { count: number; capped: boolean }): string {
  const all = touched.capped ? `${formatNumber(touched.count)}+` : formatNumber(touched.count);
  return `${formatNumber(edited)} edited of ${all} ${touched.count === 1 && !touched.capped ? "file" : "files"} touched`;
}

/** `1 resolved · 3 new findings`. */
export function findingsCounts(resolved: number, total: number): string {
  return `${formatNumber(resolved)} resolved · ${countOf(total, "new finding", "new findings")}`;
}

export function testsToRunCount(t: { total: number; files: boolean; measured: boolean }): string {
  const count = countOf(t.total, t.files ? "test file" : "test", t.files ? "test files" : "tests");
  return `${count}, ${t.measured ? "measured" : "inferred"}`;
}

export function decisionsSurfaced(titles: readonly string[]): string {
  return `${formatNumber(titles.length)}: ${titles.join("; ")}`;
}

/**
 * Lens itself calls no model. `get_answer` may have its reply written by the
 * model a repo configures, which the footer then says.
 */
export function recapFooter(modelAsks: number): string {
  if (modelAsks === 0) return `${RECAP_COPY.noModel} ${RECAP_COPY.fromIndex}`;
  const asks = countOf(modelAsks, "Ask reply", "Ask replies");
  return `${RECAP_COPY.noModel} ${asks} from get_answer may have been written by this repo's configured model.`;
}

// The brief after a compaction: a visible prompt, sent only on a press.

export const BRIEF_COPY = {
  compacted: "context compacted",
  button: "Brief Claude",
  intro: "The context was compacted. This brief is built from the Repowise index and this session's edits:",
  files: "Files edited:",
  decisions: "Decisions in play:",
  review: "Open review items:",
} as const;

/** Characters the brief may take, about 300 tokens. */
export const BRIEF_CHARS = 1_200;

export function briefDecision(title: string, reviewed: boolean): string {
  return reviewed ? `${title} (standing decision)` : `${title} (found in the code, not yet reviewed)`;
}

export function briefTests(t: { tests: string[]; measured: boolean }): string {
  return `tests to run, ${testsBasis(t.measured)}: ${t.tests.join(" ")}`;
}
