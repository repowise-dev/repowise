/**
 * The current turn for the map: which files it lit, and its story (what
 * Claude searched, opened and edited, and what the edit reaches). Read from
 * Flow's record, or, with Flow off, from the map's own trail of the session.
 * Paths come out repo-relative with `/`. Pure.
 */

import { NO_LIT, type Lit } from "../views/overlay";
import { relativeTo } from "./events";
import { visits, type Activity, type FlowState, type FlowTurn } from "./flow";
import type { Callers, TrailState } from "./trail";

export interface Story {
  searched: { pattern: string; hits: number | null }[];
  opened: { name: string; namedBy: string | null }[];
  edited: { name: string; lines: Activity["lines"] }[];
}

/** What the latest edit reaches: its importers as the graph has them so far. */
export interface Reach {
  edit: string;
  callers: Callers;
  /** Lowercased paths Claude opened this turn, to mark importers it already read. */
  opened: ReadonlySet<string>;
}

export const NO_STORY: Story = { searched: [], opened: [], edited: [] };

const nameOf = (path: string): string => path.slice(path.lastIndexOf("/") + 1);
const FILE_KINDS: ReadonlySet<string> = new Set(["read", "edit", "search"]);
const isFileStep = (a: Activity): boolean => !a.isError && FILE_KINDS.has(a.kind);
const isOpen = (a: Activity): boolean => a.kind === "read" || a.kind === "edit";

/** The latest turn in which Claude read, edited or searched: the one in progress once it touches a file, else the last that did. */
export function storyTurn(state: FlowState): FlowTurn | null {
  for (let i = state.turns.length - 1; i >= 0; i--) {
    const t = state.turns[i] as FlowTurn;
    if (t.activities.some(isFileStep)) return t;
  }
  return null;
}

function argOf(a: Activity, keys: readonly string[]): string {
  return a.args.find(([k]) => keys.includes(k))?.[1] ?? "";
}

/** One opened file once, in order; a file read then edited counts as opened once. */
function openedOf(steps: readonly Activity[]): Story["opened"] {
  const seen = new Map<string, Story["opened"][number]>();
  for (const a of steps) {
    const path = a.paths[0];
    if (!isOpen(a) || path === undefined || seen.has(path)) continue;
    seen.set(path, { name: nameOf(path), namedBy: a.namedBy?.tool ?? null });
  }
  return [...seen.values()];
}

/** The story of a turn in Flow's record. */
export function storyFromFlow(state: FlowState): Story {
  const turn = storyTurn(state);
  if (turn === null) return NO_STORY;
  const steps = turn.activities.filter(isFileStep);
  return {
    searched: steps.filter((a) => a.kind === "search").map((a) => ({ pattern: argOf(a, ["pattern", "query"]), hits: a.hits })),
    opened: openedOf(steps),
    edited: steps.filter((a) => a.kind === "edit" && a.paths[0] !== undefined).map((a) => ({ name: nameOf(a.paths[0] as string), lines: a.lines })),
  };
}

/** What a turn in Flow's record lit: its visits, and the files a Repowise reply named during it. */
export function litFromFlow(state: FlowState): Lit {
  const turn = storyTurn(state);
  if (turn === null) return NO_LIT;
  const mine = visits(state).filter((v) => v.turn === turn.seq);
  const opened = mine.filter((v) => v.kind !== "search-hit");
  const edits = opened.filter((v) => v.kind === "edit");
  return {
    hits: mine.filter((v) => v.kind === "search-hit").map((v) => v.path),
    reads: [...new Set(opened.map((v) => v.path))],
    named: Object.entries(state.named)
      .filter(([, n]) => n.turn === turn.seq)
      .map(([key]) => key),
    importers: [],
    edits: [...new Set(edits.map((v) => v.path))],
    edit: edits.at(-1)?.path ?? null,
    current: opened.at(-1)?.path ?? null,
  };
}

const rel = (abs: string, root: string, ci: boolean): string | null => relativeTo(abs, root, ci);

/** With Flow off: the map's own trail of the session, as what it lit. */
export function litFromTrail(trail: TrailState, root: string, caseInsensitive: boolean): Lit {
  const all = (paths: readonly string[]) => paths.map((p) => rel(p, root, caseInsensitive)).filter((p): p is string => p !== null);
  const reads = all(trail.reads);
  const edit = trail.edit === null ? null : rel(trail.edit, root, caseInsensitive);
  return { hits: all(trail.hits), reads, named: [], importers: [], edits: edit === null ? [] : [edit], edit, current: reads.at(-1) ?? null };
}

/** With Flow off: the trail's story, without patterns or line counts (only Flow records those). */
export function storyFromTrail(trail: TrailState): Story {
  return {
    searched: trail.searches === 0 ? [] : [{ pattern: "", hits: trail.hits.length }],
    opened: trail.reads.map((p) => ({ name: nameOf(p), namedBy: null })),
    edited: trail.edit === null ? [] : [{ name: nameOf(trail.edit), lines: null }],
  };
}

/** The importers of several edits as one answer: their union once any is in, else loading, else failed. */
export function combineCallers(answers: readonly Callers[], edited: ReadonlySet<string>): Callers | null {
  const ready = answers.filter((c): c is Extract<Callers, { status: "ready" }> => c.status === "ready");
  if (ready.length > 0) return { status: "ready", paths: [...new Set(ready.flatMap((c) => c.paths))].filter((p) => !edited.has(p.toLowerCase())) };
  if (answers.some((c) => c.status === "loading")) return { status: "loading" };
  return answers.length > 0 ? { status: "failed" } : null;
}

/**
 * The lit files with the importers of every file the turn edited added, and
 * what they reach. `importersOf` answers for a repo-relative path, or null when
 * nothing was asked for it.
 */
export function withImporters(lit: Lit, importersOf: (path: string) => Callers | null): { lit: Lit; reach: Reach | null } {
  if (lit.edit === null) return { lit, reach: null };
  const answers = lit.edits.map(importersOf).filter((c): c is Callers => c !== null);
  const callers = combineCallers(answers, new Set(lit.edits.map((p) => p.toLowerCase())));
  if (callers === null) return { lit, reach: null };
  const importers = callers.status === "ready" ? callers.paths : [];
  return { lit: { ...lit, importers }, reach: { edit: lit.edit, callers, opened: new Set(lit.reads.map((p) => p.toLowerCase())) } };
}
