/**
 * Node-id helpers, mirroring `packages/core/src/repowise/core/ids.py`.
 *
 * The backend sends node ids as prefixed strings (`external:react`,
 * `pkg:packages/core`) or as `path::Symbol`. The UI had been taking them apart
 * with ad-hoc `split("::")` and hardcoded slice lengths — `label.slice(9)` to
 * drop `external:` is correct only until somebody renames the prefix.
 *
 * Keep this in step with the Python module; the two are small and the rules
 * are the same:
 *
 * - `::` is only ever the symbol separator.
 * - An unknown prefix is not a kind, so a Windows path stays a path.
 */

/** Separator between a file path and a symbol name. */
export const SYMBOL_SEP = "::";

const KNOWN_PREFIXES = [
  "file",
  "external",
  "framework",
  "sys",
  "person",
  "pkg",
  "cmp",
  "ext",
] as const;

export type NodeKind = (typeof KNOWN_PREFIXES)[number] | "symbol" | "path";

function prefixOf(raw: string): string | null {
  const colon = raw.indexOf(":");
  if (colon < 0) return null;
  const head = raw.slice(0, colon);
  return (KNOWN_PREFIXES as readonly string[]).includes(head) ? head : null;
}

/** What kind of thing this id names. */
export function nodeKind(raw: string): NodeKind {
  if (raw.includes(SYMBOL_SEP)) return "symbol";
  const prefix = prefixOf(raw);
  return (prefix as NodeKind) ?? "path";
}

/**
 * The part after the prefix, or the id itself when there is no known prefix.
 *
 * Use instead of a hardcoded slice length: `stripPrefix("external:react")`
 * is "react" and stays correct if the prefix ever changes length.
 */
export function stripPrefix(raw: string): string {
  const prefix = prefixOf(raw);
  return prefix === null ? raw : raw.slice(prefix.length + 1);
}

/**
 * The file path an id refers to, or null when it names no file.
 *
 * A symbol resolves to the file holding it; a `file:`-prefixed KG id and a
 * bare path both resolve to themselves.
 */
export function filePathOf(raw: string): string | null {
  if (raw.includes(SYMBOL_SEP)) return raw.split(SYMBOL_SEP)[0] ?? null;
  const prefix = prefixOf(raw);
  if (prefix === null) return raw;
  if (prefix === "file") return raw.slice("file:".length);
  return null;
}

/** The symbol name in a `path::Symbol` id, or null if it names no symbol. */
export function symbolNameOf(raw: string): string | null {
  const index = raw.indexOf(SYMBOL_SEP);
  return index < 0 ? null : raw.slice(index + SYMBOL_SEP.length);
}

/** A short display label: the symbol name, or the file's basename. */
export function displayLabel(raw: string): string {
  const symbol = symbolNameOf(raw);
  if (symbol) return symbol;
  const path = filePathOf(raw);
  if (path) return path.split("/").pop() || path;
  return stripPrefix(raw);
}

/** True if the id names code we do not own (third-party or framework). */
export function isExternal(raw: string): boolean {
  const kind = nodeKind(raw);
  return kind === "external" || kind === "framework";
}
