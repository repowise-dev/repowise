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

import { isWeak, type Reach } from "./blast-map-model";

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
  bodyHeight: 380,
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
  /** Stable id: `${column}:${folder}`, or `${column}:other`. */
  key: string;
  column: number;
  /** The folder, or "N more folders" for the folded block. */
  label: string;
  /** The folders this block stands for: one, or every folded one. */
  folders: string[];
  other: boolean;
  files: number;
  /** Files with weak health (Needs work or At risk). */
  poor: number;
  /** Paths in this block, so a selection can narrow the file list. */
  paths: string[];
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
  columns: { title: string; x: number; files: number }[];
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

function folderBlocks(column: number, members: Member[]): Omit<StepBlock, "x" | "y" | "height">[] {
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
  const head = ranked.slice(0, TOP_FOLDERS).map(({ folder, ms }) => ({
    key: `${column}:${folder}`,
    column,
    label: folder,
    folders: [folder],
    other: false,
    files: ms.length,
    poor: ms.filter((m) => m.weak).length,
    paths: ms.map((m) => m.path),
  }));
  const rest = ranked.slice(TOP_FOLDERS);
  if (rest.length === 0) return head;
  const ms = rest.flatMap((r) => r.ms);
  return [
    ...head,
    {
      key: `${column}:other`,
      column,
      label: `${rest.length} more ${rest.length === 1 ? "folder" : "folders"}`,
      folders: rest.map((r) => r.folder),
      other: true,
      files: ms.length,
      poor: ms.filter((m) => m.weak).length,
      paths: ms.map((m) => m.path),
    },
  ];
}

/** Lay the reach out in step columns. */
export function buildStepMap(reach: Reach): StepMap {
  const L = STEP_LAYOUT;
  const changed = reach.changed.filter((m) => !m.isTest);
  const perColumn: Member[][] = [changed.map((m) => ({ path: m.path, weak: isWeak(m) })), [], [], []];
  const fileColumn = new Map<string, number>(changed.map((m) => [m.path, 0]));
  for (const f of reach.files) {
    const c = columnOf(f.depth);
    perColumn[c]!.push({ path: f.path, weak: isWeak(f) });
    fileColumn.set(f.path, c);
  }

  const raw = perColumn.map((ms, c) => folderBlocks(c, ms));
  const totals = raw.map((col) => col.reduce((n, b) => n + b.files, 0));
  const maxBlocks = Math.max(1, ...raw.map((col) => col.length));
  // One scale for every column, so a block's height means the same everywhere.
  const scale = (L.bodyHeight - L.gap * (maxBlocks - 1)) / Math.max(1, ...totals);
  const blocks: StepBlock[] = [];
  let bottom: number = L.top;
  raw.forEach((col, c) => {
    let y: number = L.top;
    for (const b of col) {
      const height = Math.max(L.minBlock, b.files * scale);
      blocks.push({ ...b, x: c * L.colPitch, y, height });
      y += height + L.gap;
    }
    bottom = Math.max(bottom, y - L.gap);
  });

  const blockOf = new Map<string, StepBlock>();
  for (const b of blocks) for (const p of b.paths) blockOf.set(p, b);

  // A file reached at step h joins folder(via) at step h-1 to its own folder.
  const flows = new Map<string, { from: StepBlock; to: StepBlock; files: number }>();
  for (const f of reach.files) {
    if (!f.via) continue;
    const to = blockOf.get(f.path);
    const from = blockOf.get(f.via);
    if (!to || !from || from.column !== to.column - 1) continue;
    const key = `${from.key}>${to.key}`;
    const flow = flows.get(key) ?? { from, to, files: 0 };
    flow.files += 1;
    flows.set(key, flow);
  }
  const ordered = [...flows.values()].sort(
    (a, b) => a.from.column - b.from.column || a.from.y - b.from.y || a.to.y - b.to.y,
  );
  const outSum = new Map<string, number>();
  const inSum = new Map<string, number>();
  for (const f of ordered) {
    outSum.set(f.from.key, (outSum.get(f.from.key) ?? 0) + f.files);
    inSum.set(f.to.key, (inSum.get(f.to.key) ?? 0) + f.files);
  }
  // Each end is normalized to its block: a block's outgoing bands fill its
  // height top to bottom, and so do its incoming ones.
  const outOff = new Map<string, number>();
  const inOff = new Map<string, number>();
  const inOrder = [...ordered].sort((a, b) => a.to.y - b.to.y || a.from.y - b.from.y);
  const inStart = new Map<string, number>();
  for (const f of inOrder) {
    const k = `${f.from.key}>${f.to.key}`;
    const off = inOff.get(f.to.key) ?? 0;
    inStart.set(k, off);
    inOff.set(f.to.key, off + (f.to.height * f.files) / inSum.get(f.to.key)!);
  }
  const bands: StepBand[] = ordered.map((f) => {
    const key = `${f.from.key}>${f.to.key}`;
    const h0 = (f.from.height * f.files) / outSum.get(f.from.key)!;
    const h1 = (f.to.height * f.files) / inSum.get(f.to.key)!;
    const o0 = outOff.get(f.from.key) ?? 0;
    outOff.set(f.from.key, o0 + h0);
    return {
      key,
      from: f.from.key,
      to: f.to.key,
      files: f.files,
      fromChanged: f.from.column === 0,
      x0: f.from.x + L.blockWidth,
      y0: f.from.y + o0,
      h0,
      x1: f.to.x,
      y1: f.to.y + inStart.get(key)!,
      h1,
    };
  });

  return {
    width: STEP_COLUMNS * L.colPitch,
    height: bottom + L.bottom,
    columns: totals.map((files, c) => ({ title: stepWords(c, reach.maxHop), x: c * L.colPitch, files })),
    blocks,
    bands,
    testsNotDrawn: reach.tests.length,
    changedTestsNotDrawn: reach.changed.length - changed.length,
  };
}

/** The block to open on: the step-1 block with the most poor-health files, then the most files. */
export function defaultStepBlock(map: StepMap): StepBlock | undefined {
  return map.blocks
    .filter((b) => b.column === 1)
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
