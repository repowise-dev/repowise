/**
 * The figures Flow's turn dashboard draws, derived from the activity record
 * and what the index returned. Pure. Each answers one question the chat
 * cannot: what an edit reaches that Claude did not look at, which files it
 * worked from and how it found them, what filled its context, and which
 * Repowise answers came from the index before an edit.
 */

import { isFile, pathKey, type Activity, type BlastFacts, type FlowState, type FlowTurn } from "./flow";

/** Co-change partners shown per edited file. */
const COCHANGE_SHOWN = 2;
/** Names too generic to say which tests a command ran (`runtests.py`, a `tests/` folder). */
const GENERIC = new Set(["test", "tests", "spec", "specs", "__init__"]);
/** Shell commands that run tests. */
const TEST_RUNNER = /\b(?:pytest|py\.test|manage\.py test|runtests|npm (?:run )?test|vitest|jest|go test|cargo test)\b/;

/** The blast radius response fields Flow reads; the server's own names. */
export interface BlastResponse {
  transitive_affected?: ReadonlyArray<{ path: string; depth?: number }>;
  cochange_warnings?: ReadonlyArray<{ changed: string; missing_partner: string; score: number }>;
  test_impact?: {
    files?: ReadonlyArray<{
      source_file: string;
      measured_tests?: readonly string[];
      measured_tests_total?: number;
      inferred_tests?: readonly string[];
      inferred_tests_total?: number;
    }>;
  };
}

type TestFile = NonNullable<NonNullable<BlastResponse["test_impact"]>["files"]>[number];

function testsOf(file: TestFile | undefined): BlastFacts["tests"] {
  if (file === undefined) return null;
  const measured = file.measured_tests_total ?? 0;
  if (measured > 0) return { total: measured, files: file.measured_tests ?? [], basis: "measured" };
  const inferred = file.inferred_tests_total ?? 0;
  return inferred > 0 ? { total: inferred, files: file.inferred_tests ?? [], basis: "inferred" } : null;
}

/** What one edited file reaches, from the depth-1 blast radius the map asked for. */
export function blastFacts(path: string, r: BlastResponse): BlastFacts {
  const key = pathKey(path);
  const importers = [...new Set((r.transitive_affected ?? []).map((t) => t.path))].filter((p) => pathKey(p) !== key);
  const cochange = (r.cochange_warnings ?? [])
    .filter((w) => pathKey(w.changed) === key)
    .map((w) => ({ path: w.missing_partner, score: w.score }))
    .sort((a, b) => b.score - a.score);
  const tests = testsOf((r.test_impact?.files ?? []).find((f) => pathKey(f.source_file) === key));
  return { importers, cochange, tests };
}

const allActivities = (state: FlowState): Activity[] => state.turns.flatMap((t) => t.activities);

/** Files Claude read or edited this session, as keys. */
export function openedKeys(state: FlowState): Set<string> {
  return new Set(allActivities(state).filter(isFile).map((a) => pathKey(a.paths[0] ?? "")));
}

/** The shell commands this session that ran tests. */
function testCommands(state: FlowState): string[] {
  return allActivities(state)
    .map((a) => a.command)
    .filter((c): c is string => c !== null && TEST_RUNNER.test(c));
}

const asWord = (command: string, word: string): boolean =>
  !GENERIC.has(word) && new RegExp(`(^|[^\\w])${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}($|[^\\w])`).test(command);

/**
 * Whether a test-running command names this test file: its path, its file
 * name without the extension, or its folder (`runtests.py queries` runs
 * `tests/queries/`), each as a whole word and never a generic one.
 */
function names(command: string, test: string): boolean {
  const slashed = test.replace(/\\/g, "/");
  const parts = slashed.split("/");
  const stem = (parts.at(-1) ?? "").replace(/\.[^.]+$/, "");
  const folder = parts.at(-2) ?? "";
  const cmd = command.replace(/\\/g, "/");
  return cmd.includes(slashed) || asWord(cmd, stem) || asWord(cmd, folder);
}

/** How many of these test files a test-running command this session named. */
export function testsRun(state: FlowState, tests: readonly string[]): number {
  const commands = testCommands(state);
  return tests.filter((t) => commands.some((c) => names(c, t))).length;
}

/** What one edited file reaches that Claude did not look at; only the exceptions. */
export interface Gaps {
  path: string;
  /** Direct importers, and how many Claude opened; null when it opened them all, or there are none. */
  importers: { total: number; opened: number } | null;
  /** The strongest co-change partners Claude did not open, with the server's score. */
  cochange: ReadonlyArray<{ path: string; score: number }>;
  /** Tests that reach the file, and how many a command this session ran; null when all ran, or none reach it. */
  tests: { total: number; run: number; basis: "measured" | "inferred" } | null;
}

function gapsOf(state: FlowState, path: string, facts: BlastFacts, opened: Set<string>): Gaps {
  const seen = facts.importers.filter((p) => opened.has(pathKey(p))).length;
  const t = facts.tests;
  const run = t === null ? 0 : testsRun(state, t.files);
  return {
    path,
    importers: facts.importers.length > seen ? { total: facts.importers.length, opened: seen } : null,
    cochange: facts.cochange.filter((c) => !opened.has(pathKey(c.path))).slice(0, COCHANGE_SHOWN),
    tests: t !== null && run < t.total ? { total: t.total, run, basis: t.basis } : null,
  };
}

const hasGap = (g: Gaps): boolean => g.importers !== null || g.cochange.length > 0 || g.tests !== null;

/** The files this turn edited, each once, in the order first edited. */
export function editedFiles(turn: FlowTurn): string[] {
  const out = new Map<string, string>();
  for (const a of turn.activities) {
    const path = a.kind === "edit" && !a.isError ? a.paths[0] : undefined;
    if (path !== undefined && !out.has(pathKey(path))) out.set(pathKey(path), path);
  }
  return [...out.values()];
}

/** For each file this turn edited whose blast radius landed: what it reaches that Claude did not look at. Quiet when nothing. */
export function beforeAccept(state: FlowState, turn: FlowTurn): Gaps[] {
  const opened = openedKeys(state);
  return editedFiles(turn)
    .map((path) => [path, state.blast[pathKey(path)]] as const)
    .filter((pair): pair is readonly [string, BlastFacts] => pair[1] !== undefined)
    .map(([path, facts]) => gapsOf(state, path, facts, opened))
    .filter(hasGap);
}

/** How Claude came to a file: the Repowise tool that named it first, a search that returned it, or directly. */
export type Via = { kind: "repowise"; tool: string } | { kind: "search" } | { kind: "direct" };

export interface WorkingFile {
  path: string;
  kind: "edited" | "read";
  via: Via;
}

/** Whether a search before `at` this session returned the file. */
function searchedBefore(state: FlowState, key: string, at: number): boolean {
  return allActivities(state).some((a) => a.kind === "search" && a.startedAt < at && a.hitPaths.some((p) => pathKey(p) === key));
}

function viaOf(state: FlowState, first: Activity): Via {
  if (first.namedBy !== null) return { kind: "repowise", tool: first.namedBy.tool };
  return searchedBefore(state, pathKey(first.paths[0] ?? ""), first.startedAt) ? { kind: "search" } : { kind: "direct" };
}

/** The files this turn worked on: edited first, then read, each once, with how Claude came to it. */
export function workingSet(state: FlowState, turn: FlowTurn): WorkingFile[] {
  const first = new Map<string, Activity>();
  const edited = new Set<string>();
  for (const a of turn.activities.filter((x) => isFile(x) && !x.isError && x.paths[0] !== undefined)) {
    const key = pathKey(a.paths[0] as string);
    if (!first.has(key)) first.set(key, a);
    if (a.kind === "edit") edited.add(key);
  }
  const rows = [...first].map(([key, a]): WorkingFile => ({ path: a.paths[0] as string, kind: edited.has(key) ? "edited" : "read", via: viaOf(state, a) }));
  return [...rows.filter((r) => r.kind === "edited"), ...rows.filter((r) => r.kind === "read")];
}

export type ContextKind = "repowise" | "read" | "search" | "shell" | "other";
export const CONTEXT_KINDS: readonly ContextKind[] = ["repowise", "read", "search", "shell", "other"];

const CONTEXT_OF: Record<Activity["kind"], ContextKind | null> = {
  repowise: "repowise",
  read: "read",
  search: "search",
  shell: "shell",
  edit: "other",
  other: "other",
};

/** Bytes of tool results Claude received this turn, by kind (measured from each result's text). */
export function contextBytes(turn: FlowTurn): Record<ContextKind, number> {
  const out: Record<ContextKind, number> = { repowise: 0, read: 0, search: 0, shell: 0, other: 0 };
  for (const a of turn.activities) {
    const kind = CONTEXT_OF[a.kind];
    if (kind !== null && a.bytes !== null) out[kind] += a.bytes;
  }
  return out;
}

/** How long after the turn started its first edit began; null without one. */
export function firstEditMs(turn: FlowTurn): number | null {
  const edit = turn.activities.find((a) => a.kind === "edit");
  return edit === undefined ? null : edit.startedAt - turn.startedAt;
}

/** When Claude first edited each file this session, by key. */
function firstEdits(state: FlowState): Map<string, number> {
  const out = new Map<string, number>();
  for (const a of allActivities(state)) {
    const key = a.kind === "edit" && a.paths[0] !== undefined ? pathKey(a.paths[0]) : null;
    if (key !== null && !out.has(key)) out.set(key, a.startedAt);
  }
  return out;
}

/**
 * The Repowise calls that asked about a file Claude had already edited this
 * session: the index describes the file as it was before that edit.
 */
export function answeredBeforeEdit(state: FlowState): Set<string> {
  const edits = firstEdits(state);
  const before = (a: Activity) => a.asked.some((p) => (edits.get(pathKey(p)) ?? Number.POSITIVE_INFINITY) < a.startedAt);
  return new Set(allActivities(state).filter((a) => a.kind === "repowise" && before(a)).map((a) => a.id));
}
