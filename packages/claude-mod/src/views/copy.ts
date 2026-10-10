/**
 * Every string Lens shows. Commands are written out whole so they can be
 * copied; Lens never runs them.
 */

import { formatNumber } from "@repowise-dev/ui/lib/format";
import { countOf, fit } from "../format";
import type { FileContext, HintKind, IndexFreshness, MarginNote, SavingsDelta } from "../model/session";
import type { Knows } from "../model/inspect";
import type { Squeeze } from "../model/squeeze";
import type { Behind, ReplyFact } from "../model/replies";

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

// The map: the repo as quiet tiles, lit by the turn, with its story underneath.

export const MAP_COPY = {
  title: "Lens",
  looking: "Lens map: looking for the local server",
  loading: "Lens map: loading the map",
  failed: "Lens map could not load; it tries again on /lens",
  desktop: "Lens map: not drawn on this surface (terminal and desktop app only)",
  tooLarge: "Lens map has too much detail to draw here; the terminal map shows it",
  noScore: "Not scored",
  waiting: "Lens map is ready; this pane is too narrow to open on its own. Run /lens",
  healthOn: "Health colours: on",
  healthOff: "Health colours: off",
  quiet: "Nothing lit yet this turn. Files Claude searches, opens and edits light up here.",
} as const;

/** The story strip's row heads, padded to one width. */
export const STORY_COPY = {
  searched: "SEARCHED",
  opened: "OPENED",
  edited: "EDITED",
  reaches: "REACHES",
} as const;

/** What the desktop map says to a reader that cannot see it. */
export function mapAlt(drawn: number): string {
  return `Map of the repo: ${countOf(drawn, "file", "files")} drawn as tiles, with the files Claude searched, opened and edited this turn lit, and the files that import its edit`;
}

/** /lens could not place the pane; the engine's reason names the width it needs. */
export function notPlacedLine(reason: string): string {
  return `Lens map needs a wider terminal: ${reason}`;
}

export interface ScopeFacts {
  drawn: number;
  /** Files in the feed: the largest, up to the server's cap. */
  shown: number;
  repositoryTotal: number;
  /** "2h ago", or null when the server did not say. */
  indexed: string | null;
  /** Files the server left out at its cap. */
  beyondCap: number;
  /** Too many files for a pixel each: drawn as folders. */
  dense: boolean;
  /** Files the turn touched that are not on the map (outside the feed, or outside the zoomed folder). */
  notOnMap: number;
  /** The folder zoomed into, or null. */
  zoom: string | null;
}

/** Scope parts a narrow line may leave out; the counts and the cap always stay. */
export const SCOPE_REST = "rest too small to draw";
export function indexedPart(age: string): string {
  return `indexed ${age}`;
}

/** Under the server's cap: the largest files only, and of those, as many as are drawn (or as folders). */
function cappedPart(s: ScopeFacts): string[] {
  const largest = `${formatNumber(s.shown)} largest of ${countOf(s.repositoryTotal, "file", "files")}`;
  if (s.dense) return [`${largest}; drawn as folders at this size`];
  if (s.drawn >= s.shown) return [`${largest} drawn`];
  return [`${formatNumber(s.drawn)} of the ${formatNumber(s.shown)} largest (of ${countOf(s.repositoryTotal, "file", "files")}) drawn at this size`, SCOPE_REST];
}

function drawnPart(s: ScopeFacts): string[] {
  if (s.beyondCap > 0) return cappedPart(s);
  if (s.dense) return [`${countOf(s.repositoryTotal, "file", "files")}, drawn as folders at this size`];
  if (s.drawn >= s.repositoryTotal) return [`${countOf(s.repositoryTotal, "file", "files")}, all drawn`];
  return [`${formatNumber(s.drawn)} of ${countOf(s.repositoryTotal, "file", "files")} drawn at this size`, SCOPE_REST];
}

/** The scope, as parts the pane joins with ` · ` and wraps onto two rows when narrow. */
export function scopeParts(s: ScopeFacts): string[] {
  const parts = drawnPart(s);
  if (s.indexed !== null) parts.push(indexedPart(s.indexed));
  if (s.notOnMap > 0) parts.push(`${countOf(s.notOnMap, "touched file", "touched files")} ${s.zoom === null ? "not on the map" : "outside this folder"}`);
  return parts;
}

/** `class QuerySet: 4 files`; a search with Flow off has no pattern. */
export function searchedPart(pattern: string, hits: number | null): string {
  const count = hits === null ? "matches not counted" : countOf(hits, "file", "files");
  return pattern === "" ? `last search matched ${count}` : `${pattern}: ${count}`;
}

/** An edit's size: `+1 line`, `-3 lines`, `40 lines written`; nothing when unknown. */
export function linesPart(lines: { delta: number } | { written: number } | null): string {
  if (lines === null) return "";
  if ("written" in lines) return ` ${countOf(lines.written, "line", "lines")} written`;
  const sign = lines.delta > 0 ? "+" : lines.delta < 0 ? "-" : "";
  return ` ${sign}${countOf(Math.abs(lines.delta), "line", "lines")}`;
}

export function namedByPart(name: string, tool: string): string {
  return `◆ ${name} named by ${tool}`;
}

/** `12 importers: manager.py (opened), base.py, +9`. */
export function reachesPart(total: number, names: readonly { name: string; opened: boolean }[]): string {
  const shown = names.map((n) => (n.opened ? `${n.name} (opened)` : n.name));
  const more = total - names.length;
  return `${countOf(total, "importer", "importers")}: ${[...shown, ...(more > 0 ? [`+${formatNumber(more)}`] : [])].join(", ")}`;
}

export function reachesPending(edit: string): string {
  return `finding the files that import ${edit}`;
}

export const REACHES_FAILED = "import graph unavailable";

/** The map's keys, as the hint row names them. */
export const MAP_KEYS_COPY = {
  next: "next",
  previous: "previous",
  zoom: "zoom in",
  up: "up",
  clear: "clear",
} as const;

/** Where the zoom is: `django / db / models`. */
export function crumbLine(root: string): string {
  return root.split("/").join(" / ");
}

/** Why a file is lit, in words: `Claude edited it, +1 line`, `Claude opened it`, `imports query.py`, `named by get_context`. */
export function whyParts(why: {
  edited: { lines: { delta: number } | { written: number } | null } | null;
  opened: boolean;
  imports: string | null;
  namedBy: string | null;
}): string[] {
  const parts: string[] = [];
  if (why.edited !== null) parts.push(`Claude edited it${why.edited.lines === null ? "" : `,${linesPart(why.edited.lines)}`}`);
  if (why.opened) parts.push("Claude opened it");
  if (why.imports !== null) parts.push(`imports ${why.imports}`);
  if (why.namedBy !== null) parts.push(`named by ${why.namedBy}`);
  return parts;
}

/** The inspector's line: `django/db/models/base.py · imports query.py · hotspot · 131 files use it`. */
export function detailLine(path: string, why: Parameters<typeof whyParts>[0], knows: Knows): string {
  const facts = knows.context === null ? [] : knowsParts(knows.context);
  const tests = knows.tests === null ? [] : [testsReach(knows.tests)];
  return [path, ...whyParts(why), ...facts, ...tests].join(" · ");
}

/** `12 tests reach it (inferred)`. */
export function testsReach(t: { total: number; basis: "measured" | "inferred" }): string {
  return `${countOf(t.total, "test reaches it", "tests reach it")} (${t.basis})`;
}

// The pane's tabs, Ask and Recap.

export const PANE_COPY = {
  command: "Open Lens: Claude's steps with Repowise, the map of its turn, ask the index, and the session recap",
  argumentHint: "[flow | map | ask <question> | recap]",
  tabs: { flow: "Flow", map: "Map", ask: "Ask", recap: "Recap" },
  askLabel: "ask ",
  askPlaceholder: "why ...? asks the decision records; anything else asks the index",
  askSubmit: "ask",
  askIdle: "Ask about this repo. Answers cite the evidence they used.",
  askBusy: "still answering the last question; ask again when it lands",
  noEvidence: "no evidence cited",
  fromIndex: "Built from the index",
  fromModel: "Written by this repo's configured model, from the index",
  bodyFailed: "Lens could not draw this tab; details in the debug log",
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

// Flow: the turn dashboard. Every figure is measured by Lens (times, bytes,
// counts of calls and files) or read from what Repowise returned, which says
// when it is inferred.

export const FLOW_COPY = {
  waiting: "Waiting for Claude",
  empty: "Lens is listening. Send a prompt and this tab shows what only Lens sees: what an edit reaches, where Claude's context came from, and what Repowise answered.",
  notConnected: "Repowise is not connected in this repo, so only Claude's own steps show here.",
  beforeAccept: "BEFORE YOU ACCEPT",
  workingSet: "WORKING SET",
  context: "CONTEXT",
  calls: "REPOWISE CALLS",
  asOfIndex: "as of the last index",
  edited: "edited",
  read: "read",
  search: "search",
  direct: "direct",
  hotspot: "hotspot",
  subagent: "subagent",
  asking: "asking...",
  error: "error",
  beforeEdit: "from the index before this edit",
  howAnswered: "how it was answered",
  excerpt: "reply begins",
  thenOpened: "Claude then opened",
  nothingOpened: "nothing yet",
  noRepowise: "No Repowise calls",
  editNamed: "edit landed in a file Repowise named",
  next: "Next call",
  previous: "Previous call",
} as const;

/** `0:42`: minutes and seconds since the turn started. */
export function clockText(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** `420 ms`, `2.2 s`, `41 s`, `3 min 12 s`. */
export function durationText(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)} s`;
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

/** `820 B`, `2.1 KB`, `1.2 MB`. */
export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const END_WORD = { answer: "Done", stopped: "Stopped", failed: "Did not finish" } as const;
export type EndWord = keyof typeof END_WORD;

/**
 * The status: `Working · 0:42` while Claude works, else how the turn ended
 * (`Done`, `Stopped` when aborted, `Did not finish` on a refusal or an error)
 * and how long it took; then `edited 1 file` when it edited.
 */
export function statusLine(end: EndWord | null, ms: number, edited: number): string {
  const head = end === null ? `Working · ${clockText(ms)}` : `${END_WORD[end]} · ${durationText(ms)}`;
  return edited === 0 ? head : `${head} · edited ${countOf(edited, "file", "files")}`;
}

/** `Repowise 3 calls · Claude opened 4 files, 2 named by Repowise first · edit landed in a file Repowise named`. */
export function answerLine(t: { repowise: number; opened: number; named: number; editNamed: boolean }): string {
  const parts = [t.repowise === 0 ? FLOW_COPY.noRepowise : `Repowise ${countOf(t.repowise, "call", "calls")}`];
  const named = t.named > 0 ? `, ${formatNumber(t.named)} named by Repowise first` : "";
  if (t.opened > 0) parts.push(`Claude opened ${countOf(t.opened, "file", "files")}${named}`);
  if (t.editNamed) parts.push(FLOW_COPY.editNamed);
  return parts.join(" · ");
}

/** `12 direct importers; Claude opened 2`. */
export function importersLine(total: number, opened: number): string {
  return `${countOf(total, "direct importer", "direct importers")}; Claude opened ${opened === 0 ? "none" : formatNumber(opened)}`;
}

/** `not opened, usually changes with it (co-change score): tests/queries/tests.py 0.80, sql/query.py 0.51`. */
export function cochangeLine(partners: ReadonlyArray<{ path: string; score: number }>): string {
  return `not opened, usually changes with it (co-change score): ${partners.map((p) => `${p.path} ${p.score.toFixed(2)}`).join(", ")}`;
}

/** `17 test files reach query.py (inferred); none run`. */
export function testsReachLine(total: number, basis: "measured" | "inferred", run: number, file: string): string {
  return `${countOf(total, "test file", "test files")} reach ${file} (${basis}); ${run === 0 ? "none" : formatNumber(run)} run`;
}

/** `this turn introduced 1 finding: complex method in query.py`. */
export function introducedLine(n: number, first: { biomarker: string; path: string } | null): string {
  const what = first === null ? "" : `: ${first.biomarker.replace(/_/g, " ")} in ${first.path.split("/").at(-1)}`;
  return `this turn introduced ${countOf(n, "finding", "findings")}${what}`;
}

export const HEALTH_PARTLY = "health compared in part only; no new findings in the part compared";

/** What the index knows of a file: `hotspot · 131 files use it · recent owner author_two 29 %`. */
export function knowsParts(k: { hotspot: boolean | null; callerFiles: number | null; recentOwner: { name: string; share: number } | null }): string[] {
  const parts: string[] = [];
  if (k.hotspot === true) parts.push(FLOW_COPY.hotspot);
  if (k.callerFiles !== null && k.callerFiles > 0) parts.push(countOf(k.callerFiles, "file uses it", "files use it"));
  if (k.recentOwner !== null) parts.push(`recent owner ${k.recentOwner.name} ${Math.round(k.recentOwner.share * 100)} %`);
  return parts;
}

export function moreFiles(n: number): string {
  return `+${formatNumber(n)} more`;
}

export const CONTEXT_LABEL = { repowise: "Repowise", read: "file reads", search: "search", shell: "shell", other: "other" } as const;

/** `Repowise 7.4 KB`. */
export function contextPart(kind: keyof typeof CONTEXT_LABEL, bytes: number): string {
  return `${CONTEXT_LABEL[kind]} ${sizeText(bytes)}`;
}

export function firstEditText(ms: number): string {
  return `first edit after ${durationText(ms)}`;
}

/** An earlier turn in one line: `Turn 2 · 41 s · 2 Repowise calls · 3 files read · 0 edited`, saying when it stopped. */
export function turnFooter(turn: number, ms: number | null, t: { repowise: number; reads: number; edits: number }, end: EndWord | null = "answer"): string {
  const how = end === null || end === "answer" ? "" : ` · ${END_WORD[end].toLowerCase()}`;
  const took = ms === null ? "" : ` · ${durationText(ms)}`;
  return `Turn ${turn}${how}${took} · ${countOf(t.repowise, "Repowise call", "Repowise calls")} · ${countOf(t.reads, "file read", "files read")} · ${formatNumber(t.edits)} edited`;
}

export function earlierLine(t: { turns: number; repowise: number; reads: number; edits: number }): string {
  return `Earlier: ${countOf(t.turns, "turn", "turns")} · ${countOf(t.repowise, "Repowise call", "Repowise calls")} · ${countOf(t.reads, "file read", "files read")} · ${formatNumber(t.edits)} edited`;
}

export function hiddenSteps(n: number): string {
  return `${countOf(n, "earlier call", "earlier calls")} hidden · j / k step through calls`;
}

export function laterSteps(n: number): string {
  return `${countOf(n, "later call", "later calls")} below · j / k step through calls`;
}

export function droppedSteps(n: number): string {
  return `${countOf(n, "earlier step", "earlier steps")} of this turn not kept`;
}

const INSIDE_UNITS: Record<string, [string, string]> = {
  targets: ["target", "targets"],
  docs: ["documentation page", "documentation pages"],
  symbols: ["symbol", "symbols"],
  callers: ["caller", "callers"],
  callees: ["callee", "callees"],
  decisions: ["decision", "decisions"],
  hotspots: ["hotspot", "hotspots"],
  dependents: ["direct dependent", "direct dependents"],
  coChange: ["co-change partner", "co-change partners"],
  contributors: ["contributor", "contributors"],
  citations: ["cited file", "cited files"],
  bodies: ["symbol body", "symbol bodies"],
  rationale: ["rationale comment", "rationale comments"],
  guesses: ["best guess", "best guesses"],
  commits: ["commit", "commits"],
  results: ["result", "results"],
  lines: ["line", "lines"],
};

/** `40 of 180 symbols`, `12 direct dependents`; a key the reply named, as it named it. */
export function insideFact(f: ReplyFact): string {
  const [one, many] = INSIDE_UNITS[f.what] ?? [f.what.replace(/_/g, " "), f.what.replace(/_/g, " ")];
  return f.of === undefined ? countOf(f.n, one, many) : `${formatNumber(f.n)} of ${countOf(f.of, one, many)}`;
}

/** How fresh the index the reply came from was: the local server's word, else the index's age. */
export function freshnessText(b: Pick<Behind, "indexBehind" | "ageDays">): string | null {
  if (b.indexBehind !== null) return b.indexBehind ? "index behind HEAD" : "index current";
  return b.ageDays === null ? null : `index ${countOf(b.ageDays, "day", "days")} old`;
}

const ageText = (days: number): string => `index ${countOf(days, "day", "days")} old`;
const when = (shown: boolean, text: string): string | null => (shown ? text : null);
const named = <T>(v: T | null, text: (v: T) => string): string | null => (v === null ? null : text(v));

/** How the reply says it was answered, in the order the detail lists it; each part only when the reply said it. */
const BEHIND: ReadonlyArray<(b: Behind, bytes: number | null) => string | null> = [
  (b) => named(b.commit, (c) => `indexed at ${c}`),
  (b) => named(b.ageDays, ageText),
  (b) => named(b.verified, (v) => (v ? "verified against the code" : "not verified")),
  (b) => when(b.complete === false, "partial: the server capped it"),
  (b) => named(b.confidence, (c) => `confidence ${c}`),
  (b) => named(b.grounding, (g) => `grounding ${g.replace(/_/g, " ")}`),
  (b) => named(b.retrieval, (r) => `retrieval ${r}`),
  (b, bytes) => named(b.budget, (budget) => `reply ${bytes === null ? countOf(budget.used, "character", "characters") : sizeText(bytes)} (cap ${countOf(budget.limit, "character", "characters")})`),
  (b) => named(b.omittedTokens, (t) => `${countOf(t, "token", "tokens")} left out, restorable`),
  (b) => named(b.degraded, (d) => `degraded: ${d}`),
  (b) => when(b.semantic === false, "semantic search off"),
];

export function behindParts(b: Behind, bytes: number | null = null): string[] {
  return BEHIND.map((part) => part(b, bytes)).filter((p): p is string => p !== null);
}

