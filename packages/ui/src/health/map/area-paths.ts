/**
 * Walking the area hierarchy the way the map shows it.
 *
 * A directory holding one sub-directory and no files of its own is not worth a
 * click (`packages/core/src/repowise/core` is four of them in a row), so a
 * chain like that is drawn as one disc named by the whole chain, and every
 * walk here steps over it. The geometry is unchanged by the collapse: a lone
 * child fills its parent, so the disc is the same disc either way.
 *
 * Pure.
 */

import { ROOT_AREA, dirOf } from "../../lib/areas";
import type { AreaIndex, MapArea } from "./areas";

const childrenOf = (index: AreaIndex, path: string): readonly string[] =>
  index.children.get(path) ?? [];

/** An expanded area with one child and no files of its own: drawn as its child. */
export function isPassThrough(index: AreaIndex, path: string): boolean {
  const a = index.areas.get(path);
  return (
    a !== undefined &&
    index.expanded.has(path) &&
    a.directFileCount === 0 &&
    childrenOf(index, path).length === 1
  );
}

/** Where a chain starting at `path` ends: the area actually drawn for it. */
export function chainEnd(index: AreaIndex, path: string): string {
  let cur = path;
  while (cur !== ROOT_AREA && isPassThrough(index, cur)) cur = childrenOf(index, cur)[0]!;
  return cur;
}

/** The discs drawn inside `path`, biggest first. */
export function displayChildren(index: AreaIndex, path: string): string[] {
  return childrenOf(index, path).map((c) => chainEnd(index, c));
}

/** The disc that contains `path`'s disc, or `null` for the root. */
export function displayParent(index: AreaIndex, path: string): string | null {
  if (path === ROOT_AREA) return null;
  let p = index.areas.get(path)?.parent ?? dirOf(path);
  while (p !== ROOT_AREA && isPassThrough(index, p)) p = index.areas.get(p)?.parent ?? dirOf(p);
  return p;
}

/** A disc's name: its path below the disc containing it, so a chain names itself. */
export function displayLabel(index: AreaIndex, path: string, rootLabel: string): string {
  if (path === ROOT_AREA) return rootLabel;
  const parent = displayParent(index, path);
  return parent ? path.slice(parent.length + 1) : path;
}

/** The discs from the root down to `path`'s, for a breadcrumb. */
export function areaTrail(index: AreaIndex, path: string): string[] {
  const out: string[] = [];
  let cur: string | null = chainEnd(index, path);
  while (cur !== null) {
    out.push(cur);
    cur = displayParent(index, cur);
  }
  return out.reverse();
}

/** Whether `path` is `area` or inside it. */
export function isWithin(path: string, area: string): boolean {
  return area === ROOT_AREA || path === area || path.startsWith(`${area}/`);
}

/**
 * Lines in an area's own files, outside its sub-directories. Floored at one
 * line a file, so a directory of tiny files still gets a visible slot.
 */
export function directNloc(index: AreaIndex, a: MapArea): number {
  const kids = childrenOf(index, a.path).reduce((s, c) => s + (index.areas.get(c)?.nloc ?? 0), 0);
  return Math.max(a.nloc - kids, a.directFileCount);
}

/** Where one file is drawn: its area's own-files slot, or a whole area not yet unfolded. */
export interface FileHost {
  area: string;
  /** True: the area's own-files slot. False: the area's whole disc. */
  direct: boolean;
}

export function hostKey(h: FileHost): string {
  return `${h.direct ? "d" : "w"}:${h.area}`;
}

/**
 * The host a file lands in: the deepest drawn area holding it. A file in a
 * directory the hierarchy has not unfolded yet lands in the deepest unfolded
 * area's disc; a file in a directory with no rollup at all (nothing in it is
 * scored) lands in its nearest area's own-files slot when there is one.
 * `null` when it has nowhere to go.
 */
export function hostOf(index: AreaIndex, filePath: string): FileHost | null {
  const dir = dirOf(filePath);
  let cur = ROOT_AREA;
  if (!index.areas.has(cur)) return null;
  for (;;) {
    if (dir === cur) break;
    if (!index.expanded.has(cur)) return { area: cur, direct: false };
    const next = displayChildren(index, cur).find((c) => isWithin(dir, c));
    if (next === undefined) break;
    cur = next;
  }
  if (!index.expanded.has(cur)) return { area: cur, direct: false };
  return index.areas.get(cur)!.directFileCount > 0 ? { area: cur, direct: true } : null;
}
