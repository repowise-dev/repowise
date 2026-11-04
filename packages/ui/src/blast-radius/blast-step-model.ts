/**
 * The blast radius as steps, left to right: the changed files, then the
 * source files one, two and three imports away, each column split into
 * folder blocks sized by file count, with bands from each folder to the
 * folders it reaches at the next step. Pure and deterministic: the same
 * reach always lays out the same way.
 *
 * Tests are never drawn. A test that imports the change is a test to run,
 * not reach; `readReach` already keeps them out of `files`, and changed
 * test files are dropped here.
 */

import { isWeak, type FileMarks, type Reach } from "./blast-map-model";

/** Columns: changed, then 1, 2 and 3 steps away. Deeper files fold into the last. */
export const STEP_COLUMNS = 4;
/** Folder blocks per column before the rest fold into one "N more folders" block. */
export const TOP_FOLDERS = 5;

/** Layout constants, in SVG user units (1 unit = 1 CSS pixel; the drawing never scales). */
export const STEP_LAYOUT = {
  colPitch: 232,
  blockWidth: 14,
  top: 28,
  bottom: 8,
  /** Room for the tallest column's blocks; columns with more files are scaled to fit. */
  bodyHeight: 300,
  /** A block is never shorter than one label line, and blocks keep a gap, so labels never overlap. */
  minBlock: 18,
  gap: 12,
  labelGap: 7,
} as const;

/**
 * A file's folder for the picture: its package, then the first folder under
 * the package's source root. `packages/server/src/repowise/server/mcp_server/x.py`
 * is `server/mcp_server`; a file at the package's root is `server (top)`.
 *
 * A path heuristic, deliberately: it reads the common monorepo and `src/`
 * layouts with no index field. If a layout reads badly, the upgrade is a
 * per-file folder from the server's module map.
 */
export function stepFolderOf(path: string): string {
  const parts = path.split("/");
  const dirs = parts.slice(0, -1);
  if (dirs.length === 0) return "(root)";
  let pkg: string;
  let rest: string[];
  if (CONTAINERS.has(dirs[0]!) && dirs.length > 1) {
    pkg = dirs[1]!;
    rest = dirs.slice(2);
  } else {
    pkg = dirs[0]!;
    rest = dirs.slice(1);
  }
  if (rest[0] === "src" || rest[0] === "lib") rest = rest.slice(1);
  // A namespace or import root that repeats the package's name (`repowise/server`,
  // `server`) is the package itself, not a folder in it.
  const own = rest.slice(0, 3).indexOf(pkg);
  if (own >= 0) rest = rest.slice(own + 1);
  return rest.length > 0 ? `${pkg}/${rest[0]}` : `${pkg} (top)`;
}

const CONTAINERS = new Set(["packages", "apps", "libs", "services", "crates", "modules", "plugins"]);

export interface StepBlock {
  /** Stable id: `${column}:${folder}`, `${column}:other` or `${column}:less`. */
  key: string;
  column: number;
  /** The folder, "N more folders" for the folded block, or "Show fewer folders". */
  label: string;
  /** The folders this block stands for: one, or every folded one. */
  folders: string[];
  /** The folded "N more folders" block: picking it opens the column. */
  other: boolean;
  /** The "Show fewer folders" control at the foot of an opened column. */
  less: boolean;
  files: number;
  /** Files with weak health (Needs work or At risk). */
  poor: number;
  /** Paths in this block, so a selection can narrow the file list. */
  paths: string[];
  /** Up to three file names to name it by: poor health first, then by name. */
  top: string[];
  x: number;
  y: number;
  height: number;
}

export interface StepBand {
  key: string;
  from: string;
  to: string;
  files: number;
  /** Leaves the changed column: drawn in the change's colour. */
  fromChanged: boolean;
  /** The files this link pulls in: reached at the right end through a file at the left end. */
  paths: string[];
  /** The band's left end (top and bottom) and right end, normalized to fit each block. */
  x0: number;
  y0: number;
  h0: number;
  x1: number;
  y1: number;
  h1: number;
}

export interface StepMap {
  width: number;
  height: number;
  /** Each column's title, x, file count, and whether its folded folders are open. */
  columns: { title: string; x: number; files: number; expanded: boolean }[];
  blocks: StepBlock[];
  bands: StepBand[];
  /** Reached test files, counted for the stats and never drawn. */
  testsNotDrawn: number;
  /** Changed test files, also never drawn. */
  changedTestsNotDrawn: number;
}


function columnOf(depth: number): number {
  return Math.min(Math.max(depth, 1), STEP_COLUMNS - 1);
}

interface Member {
  path: string;
  weak: boolean;
}

function baseName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

function topNames(ms: Member[]): string[] {
  return [...ms]
    .sort((a, b) => Number(b.weak) - Number(a.weak) || baseName(a.path).localeCompare(baseName(b.path)))
    .slice(0, 3)
    .map((m) => baseName(m.path));
}

type RawBlock = Omit<StepBlock, "x" | "y" | "height">;

function folderBlocks(column: number, members: Member[], expanded: boolean): RawBlock[] {
  const byFolder = new Map<string, Member[]>();
  for (const m of members) {
    const f = stepFolderOf(m.path);
    const list = byFolder.get(f);
    if (list) list.push(m);
    else byFolder.set(f, [m]);
  }
  const ranked = [...byFolder.entries()]
    .map(([folder, ms]) => ({ folder, ms }))
    .sort((a, b) => b.ms.length - a.ms.length || a.folder.localeCompare(b.folder));
  const folder = ({ folder, ms }: { folder: string; ms: Member[] }): RawBlock => ({
    key: `${column}:${folder}`,
    column,
    label: folder,
    folders: [folder],
    other: false,
    less: false,
    files: ms.length,
    poor: ms.filter((m) => m.weak).length,
    paths: ms.map((m) => m.path),
    top: topNames(ms),
  });
  const rest = ranked.slice(TOP_FOLDERS);
  if (rest.length === 0) return ranked.map(folder);
  if (expanded)
    return [
      ...ranked.map(folder),
      {
        key: `${column}:less`,
        column,
        label: "Show fewer folders",
        folders: [],
        other: false,
        less: true,
        files: 0,
        poor: 0,
        paths: [],
        top: [],
      },
    ];
  const ms = rest.flatMap((r) => r.ms);
  return [
    ...ranked.slice(0, TOP_FOLDERS).map(folder),
    {
      key: `${column}:other`,
      column,
      label: `${rest.length} more ${rest.length === 1 ? "folder" : "folders"}`,
      folders: rest.map((r) => r.folder),
      other: true,
      less: false,
      files: ms.length,
      poor: ms.filter((m) => m.weak).length,
      paths: ms.map((m) => m.path),
      top: topNames(ms),
    },
  ];
}

/** Each column's files: the changed sources first, then the reached files by step. */
function columnMembers(reach: Reach, changed: FileMarks[]): Member[][] {
  const perColumn: Member[][] = [changed.map((m) => ({ path: m.path, weak: isWeak(m) })), [], [], []];
  for (const f of reach.files) perColumn[columnOf(f.depth)]!.push({ path: f.path, weak: isWeak(f) });
  return perColumn;
}

/**
 * Blocks placed top to bottom in their columns. One scale for every column,
 * so a block's height means the same everywhere; every block starts at the
 * label's height and grows with its files, so the tallest column never
 * passes the body height, however lopsided the reach.
 */
function placeBlocks(raw: RawBlock[][], totals: number[]): { blocks: StepBlock[]; bottom: number } {
  const L = STEP_LAYOUT;
  const maxBlocks = Math.max(1, ...raw.map((col) => col.length));
  const scale = Math.max(0, L.bodyHeight - L.gap * (maxBlocks - 1) - L.minBlock * maxBlocks) / Math.max(1, ...totals);
  const blocks: StepBlock[] = [];
  let bottom: number = L.top;
  raw.forEach((col, c) => {
    let y: number = L.top;
    for (const b of col) {
      const height = L.minBlock + b.files * scale;
      blocks.push({ ...b, x: c * L.colPitch, y, height });
      y += height + L.gap;
    }
    bottom = Math.max(bottom, y - L.gap);
  });
  return { blocks, bottom };
}

interface Flow {
  from: StepBlock;
  to: StepBlock;
  files: number;
  paths: string[];
}

/** A file reached at step h joins folder(via) at step h-1 to its own folder; one flow per pair of blocks. */
function collectFlows(reach: Reach, blocks: StepBlock[]): Flow[] {
  const blockOf = new Map<string, StepBlock>();
  for (const b of blocks) for (const p of b.paths) blockOf.set(p, b);
  const flows = new Map<string, Flow>();
  for (const f of reach.files) {
    const to = blockOf.get(f.path);
    const from = f.via ? blockOf.get(f.via) : undefined;
    if (!to || !from || from.column !== to.column - 1) continue;
    const key = `${from.key}>${to.key}`;
    const flow = flows.get(key) ?? { from, to, files: 0, paths: [] };
    flow.files += 1;
    flow.paths.push(f.path);
    flows.set(key, flow);
  }
  return [...flows.values()].sort((a, b) => a.from.column - b.from.column || a.from.y - b.from.y || a.to.y - b.to.y);
}

function sumBy(flows: Flow[], end: (f: Flow) => StepBlock): Map<string, number> {
  const sums = new Map<string, number>();
  for (const f of flows) sums.set(end(f).key, (sums.get(end(f).key) ?? 0) + f.files);
  return sums;
}

/**
 * Bands from flows, each end normalized to its block: a block's outgoing
 * bands fill its height top to bottom, and so do its incoming ones.
 */
function shapeBands(ordered: Flow[]): StepBand[] {
  const outSum = sumBy(ordered, (f) => f.from);
  const inSum = sumBy(ordered, (f) => f.to);
  const inOff = new Map<string, number>();
  const inStart = new Map<string, number>();
  for (const f of [...ordered].sort((a, b) => a.to.y - b.to.y || a.from.y - b.from.y)) {
    const off = inOff.get(f.to.key) ?? 0;
    inStart.set(`${f.from.key}>${f.to.key}`, off);
    inOff.set(f.to.key, off + (f.to.height * f.files) / inSum.get(f.to.key)!);
  }
  const outOff = new Map<string, number>();
  return ordered.map((f) => {
    const key = `${f.from.key}>${f.to.key}`;
    const h0 = (f.from.height * f.files) / outSum.get(f.from.key)!;
    const o0 = outOff.get(f.from.key) ?? 0;
    outOff.set(f.from.key, o0 + h0);
    return {
      key,
      from: f.from.key,
      to: f.to.key,
      files: f.files,
      paths: f.paths,
      fromChanged: f.from.column === 0,
      x0: f.from.x + STEP_LAYOUT.blockWidth,
      y0: f.from.y + o0,
      h0,
      x1: f.to.x,
      y1: f.to.y + inStart.get(key)!,
      h1: (f.to.height * f.files) / inSum.get(f.to.key)!,
    };
  });
}

/** Lay the reach out in step columns; *expanded* columns show every folder instead of folding the rest. */
export function buildStepMap(reach: Reach, expanded: ReadonlySet<number> = new Set()): StepMap {
  const L = STEP_LAYOUT;
  const changed = reach.changed.filter((m) => !m.isTest);
  const raw = columnMembers(reach, changed).map((ms, c) => folderBlocks(c, ms, expanded.has(c)));
  const totals = raw.map((col) => col.reduce((n, b) => n + b.files, 0));
  const { blocks, bottom } = placeBlocks(raw, totals);
  return {
    width: STEP_COLUMNS * L.colPitch,
    height: bottom + L.bottom,
    columns: totals.map((files, c) => ({
      title: stepWords(c, reach.maxHop),
      x: c * L.colPitch,
      files,
      expanded: raw[c]!.some((b) => b.less),
    })),
    blocks,
    bands: shapeBands(collectFlows(reach, blocks)),
    testsNotDrawn: reach.tests.length,
    changedTestsNotDrawn: reach.changed.length - changed.length,
  };
}

/** The block to open on: the step-1 folder with the most poor-health files, then the most files (never a folded block). */
export function defaultStepBlock(map: StepMap): StepBlock | undefined {
  return map.blocks
    .filter((b) => b.column === 1 && !b.less && !b.other)
    .sort((a, b) => b.poor - a.poor || b.files - a.files || a.label.localeCompare(b.label))[0];
}

/** The reached files a block stands for in the "files that depend on this" list. */
export function blockFocusPaths(block: StepBlock, reach: Reach): Set<string> {
  if (block.column > 0) return new Set(block.paths);
  // A changed folder stands for what imports it directly.
  const from = new Set(block.paths);
  return new Set(reach.files.filter((f) => f.depth === 1 && f.via && from.has(f.via)).map((f) => f.path));
}

/** A column's name; the last reads "3+ steps" when deeper files folded into it. */
export function stepWords(column: number, maxHop = 0): string {
  if (column === 0) return "Changed";
  if (column === 1) return "1 step";
  return column === STEP_COLUMNS - 1 && maxHop > column ? `${column}+ steps` : `${column} steps`;
}

export interface StepPath {
  blocks: Set<string>;
  bands: Set<string>;
}

/**
 * Everything a block is joined to through the bands: upstream back to the
 * changed folders and downstream to the last step, so a highlight shows how
 * the change reaches it and what it passes on.
 */
export function pathThrough(map: StepMap, key: string): StepPath {
  const blocks = new Set<string>([key]);
  const bands = new Set<string>();
  const walk = (start: string, side: "from" | "to") => {
    const next = side === "to" ? "from" : "to";
    const queue = [start];
    const seen = new Set<string>([start]);
    while (queue.length > 0) {
      const at = queue.shift()!;
      for (const b of map.bands) {
        if (b[side] !== at) continue;
        bands.add(b.key);
        const other = b[next];
        blocks.add(other);
        if (!seen.has(other)) {
          seen.add(other);
          queue.push(other);
        }
      }
    }
  };
  walk(key, "to"); // upstream: bands that arrive here
  walk(key, "from"); // downstream: bands that leave here
  return { blocks, bands };
}

/** The block a file sits in, for linking a list row back to the drawing. */
export function blockOfPath(map: StepMap, path: string): StepBlock | undefined {
  return map.blocks.find((b) => b.paths.includes(path));
}

export type StepDirection = "up" | "down" | "left" | "right";

/**
 * The block an arrow key moves to: up and down within a column, left and
 * right to the block nearest in height in the next column that has any.
 */
export function neighborBlock(map: StepMap, key: string, dir: StepDirection): StepBlock | undefined {
  const at = map.blocks.find((b) => b.key === key);
  if (!at) return map.blocks[0];
  if (dir === "up" || dir === "down") {
    const col = map.blocks.filter((b) => b.column === at.column);
    const i = col.indexOf(at) + (dir === "down" ? 1 : -1);
    return col[i];
  }
  const step = dir === "right" ? 1 : -1;
  const mid = at.y + at.height / 2;
  for (let c = at.column + step; c >= 0 && c < STEP_COLUMNS; c += step) {
    const col = map.blocks.filter((b) => b.column === c);
    if (col.length === 0) continue;
    return col.reduce((best, b) =>
      Math.abs(b.y + b.height / 2 - mid) < Math.abs(best.y + best.height / 2 - mid) ? b : best,
    );
  }
  return undefined;
}
