/**
 * Areas: a directory at some depth, the unit every lens groups files by.
 *
 * Pure path arithmetic so any surface (a queue grouped by area, the area map,
 * an area card) names the same area for the same file. Paths are
 * repository-relative with `/` separators, as the index stores them.
 */

/** The area of files that sit at the repository root. */
export const ROOT_AREA = "";

/** The directory part of a path, without the file name. */
export function dirOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? ROOT_AREA : path.slice(0, i);
}

/** The first `depth` directories of `path`'s directory, or the root area. */
export function areaOf(path: string, depth: number): string {
  const dir = dirOf(path);
  if (!dir || depth <= 0) return ROOT_AREA;
  return dir.split("/").slice(0, depth).join("/");
}

/**
 * The depth at which `paths` first separate: one level below the directory
 * they all share, so a repository whose code all lives under `src/` groups by
 * `src/<area>` rather than putting everything in one `src` area. Never below
 * 1, and never deeper than `maxDepth`.
 */
export function separatingDepth(paths: readonly string[], maxDepth = 8): number {
  const dirs = paths.map((p) => dirOf(p).split("/").filter(Boolean));
  if (dirs.length === 0) return 1;
  let shared = 0;
  const first = dirs[0]!;
  while (
    shared < first.length &&
    dirs.every((d) => d.length > shared + 1 && d[shared] === first[shared])
  ) {
    shared += 1;
  }
  return Math.min(Math.max(shared + 1, 1), maxDepth);
}

export interface AreaGroup<T> {
  area: string;
  items: T[];
}

/**
 * Items grouped by area, groups in first-seen order and items in their given
 * order, so a ranked list stays ranked inside each group.
 */
export function groupByArea<T>(
  items: readonly T[],
  pathOf: (item: T) => string,
  depth: number,
): AreaGroup<T>[] {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const area = areaOf(pathOf(item), depth);
    const bucket = groups.get(area);
    if (bucket) bucket.push(item);
    else groups.set(area, [item]);
  }
  return Array.from(groups, ([area, grouped]) => ({ area, items: grouped }));
}

/**
 * Whether `path` sits inside `area`, at any depth. The root area is the files
 * at the repository root, as `groupByArea` groups them, not every path.
 */
export function inArea(path: string, area: string): boolean {
  return area === ROOT_AREA ? dirOf(path) === ROOT_AREA : path.startsWith(`${area}/`);
}
