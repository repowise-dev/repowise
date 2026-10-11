/**
 * The map's inspector: a cursor over the files the turn lit, in a fixed order
 * (the edits, then the files Claude opened, then the edit's other importers, then
 * files a Repowise reply named), and what one of them is: why it is lit and
 * what the index already told Lens about it. Nothing here asks for more. Pure.
 */

import type { Lit } from "../views/overlay";
import { pathKey, type FlowState } from "./flow";
import type { FileContext } from "./session";
import type { Story } from "./story";

/** What Lens already knows of a file, from lookups it made anyway. */
export interface Knows {
  /** The file's card (get_context), when the spinner fetched it. */
  context: FileContext | null;
  /** Tests that reach it (the edit's blast radius), when known. */
  tests: { total: number; basis: "measured" | "inferred" } | null;
  /** The Repowise tool whose reply named it, when one did. */
  namedBy: string | null;
}

export const KNOWS_NOTHING: Knows = { context: null, tests: null, namedBy: null };

const key = (p: string): string => p.toLowerCase();

/** The lit files in cursor order, each once. */
export function litPaths(lit: Lit): string[] {
  const edits = [...lit.edits].reverse();
  const order = [...edits, ...lit.reads, ...lit.importers, ...lit.named];
  const seen = new Set<string>();
  return order.filter((p) => {
    if (seen.has(key(p))) return false;
    seen.add(key(p));
    return true;
  });
}

/** The next (`by` 1) or previous (-1) lit file after `current`, wrapping; the first or last when `current` is not lit. */
export function step(paths: readonly string[], current: string | null, by: 1 | -1): string | null {
  if (paths.length === 0) return null;
  const at = current === null ? -1 : paths.findIndex((p) => key(p) === key(current));
  if (at === -1) return (by === 1 ? paths[0] : paths.at(-1)) ?? null;
  return paths[(at + by + paths.length) % paths.length] ?? null;
}

/** Why a file is lit, as facts the copy words: edited (with its size), opened, an importer of the edit, named. */
export interface Why {
  edited: { lines: Story["edited"][number]["lines"] } | null;
  opened: boolean;
  imports: string | null;
  namedBy: string | null;
}

const nameOf = (p: string): string => p.slice(p.lastIndexOf("/") + 1);
const has = (paths: readonly string[], p: string): boolean => paths.some((x) => key(x) === key(p));

export function whyLit(path: string, lit: Lit, story: Story, knows: Knows): Why {
  const edit = has(lit.edits, path) ? (story.edited.find((e) => e.name === nameOf(path)) ?? { lines: null }) : null;
  return {
    edited: edit === null ? null : { lines: edit.lines },
    opened: edit === null && has(lit.reads, path),
    imports: has(lit.importers, path) && lit.edit !== null ? nameOf(lit.edit) : null,
    namedBy: knows.namedBy,
  };
}

/** A file's card by repo-relative path, case aside. */
function contextOf(contexts: Readonly<Record<string, FileContext>>, path: string): FileContext | null {
  const exact = contexts[path];
  if (exact !== undefined) return exact;
  const lower = key(path);
  const found = Object.keys(contexts).find((k) => key(k) === lower);
  return found === undefined ? null : (contexts[found] ?? null);
}

/** What Lens already holds about a file: its card, the tests that reach an edited one, and which reply named it (Flow's record, when on). */
export function knowsOf(path: string, contexts: Readonly<Record<string, FileContext>>, flow: FlowState | null): Knows {
  const facts = flow === null ? undefined : flow.blast[pathKey(path)];
  const tests = facts?.tests ?? null;
  return {
    context: contextOf(contexts, path),
    tests: tests === null ? null : { total: tests.total, basis: tests.basis },
    namedBy: flow === null ? null : (flow.named[pathKey(path)]?.tool ?? null),
  };
}
