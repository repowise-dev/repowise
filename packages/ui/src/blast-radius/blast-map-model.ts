/**
 * The facts a change review reads from one blast-radius result, and the
 * picture drawn from them. Pure: the same result always reads, and lays out,
 * the same way.
 *
 * Tests are counted apart: a test that imports the changed code is a test to
 * run, never code the change reaches. Counting them as reach made a one-file
 * change read as "2,295 files reached", 2,095 of them tests.
 *
 * The picture is file by file when the reach is small enough to read that
 * way, and module by module otherwise: past a dozen or so files, per-file
 * dots are a hairball that says nothing, and a module bubble keeps the count
 * and the one mark that matters (files that change often with no test).
 */

import type { AffectedSignal, BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { HEALTH_BAND_LABEL, formatScore, isWeakHealthBand, bandForScore } from "@repowise-dev/types/health";

type Result = BlastRadiusResponse & { affected_signals?: AffectedSignal[] };

/** What the index knows about a file. `null` is unknown, never "fine". */
export interface FileMarks {
  path: string;
  healthScore: number | null;
  isHotspot: boolean | null;
  /** Listed by the analysis as having no test evidence at all. */
  untested: boolean;
}

/** A file the change reaches: how many imports away, through which file, in which module. */
export interface ReachedFile extends FileMarks {
  depth: number;
  via: string | null;
  module: string;
}

/** One analysis read once: changed files, the source files it reaches, the tests apart. */
export interface Reach {
  changed: FileMarks[];
  /** Source files that import the change, nearest first. Tests excluded. */
  files: ReachedFile[];
  /** Test files that import the change at any depth: run them, never count them as reach. */
  tests: string[];
  direct: number;
  modules: number;
  maxHop: number;
}

/** A file's module: the index's own when health scored it, else its folder two levels down. */
export function moduleOf(path: string, signal: AffectedSignal | undefined): string {
  if (signal?.module) return signal.module;
  const parts = path.split("/");
  if (parts.length <= 1) return "(root)";
  return parts.slice(0, Math.min(2, parts.length - 1)).join("/");
}

const TEST_PATH = /(^|\/)(tests?|__tests__|specs?)\/|(^|\/)test_[^/]+\.py$|_test\.(py|go|rb)$|\.(test|spec)\.[cm]?[jt]sx?$/;

/**
 * Whether a path is a test: the index says so, or it is named the way every
 * common runner names tests (an older server sends no flag, and an index can
 * miss a suite its config does not declare).
 */
export function isTestFile(path: string, signal: AffectedSignal | undefined): boolean {
  return Boolean(signal?.is_test) || TEST_PATH.test(path);
}

export function isWeak(m: FileMarks): boolean {
  return m.healthScore !== null && isWeakHealthBand(bandForScore(m.healthScore));
}

/** How much a file asks for attention: changed often, weak health, no tests. */
export function attention(m: FileMarks): number {
  return (m.isHotspot ? 1 : 0) + (isWeak(m) ? 1 : 0) + (m.untested ? 1 : 0);
}

/** Changed often and nothing tests it: the one mark the map draws. */
export function needsLook(m: FileMarks): boolean {
  return Boolean(m.isHotspot) && m.untested;
}

/** Every mark a file carries, in words, for tooltips and the review prompt. */
export function describeMarks(m: FileMarks): string[] {
  const words: string[] = [];
  if (m.healthScore !== null)
    words.push(`${HEALTH_BAND_LABEL[bandForScore(m.healthScore)]} health ${formatScore(m.healthScore)}`);
  else words.push("Health not scored");
  if (m.isHotspot) words.push("Changed often");
  if (m.untested) words.push("No test found");
  return words;
}

function byAttentionThenPath(a: FileMarks, b: FileMarks): number {
  return attention(b) - attention(a) || a.path.localeCompare(b.path);
}

const PACKAGE_INIT = /(^|\/)__init__\.py$/;

function marksReader(result: Result): (path: string) => FileMarks {
  const signals = new Map((result.affected_signals ?? []).map((s) => [s.path, s]));
  const gaps = new Set(result.test_gaps);
  // Without signals (an older server, a manual file pick there) the changed
  // files still carry the analysis's own hotspot verdict.
  const hotspotFallback = new Map(result.direct_risks.map((d) => [d.path, d.is_hotspot]));
  return (path) => {
    const s = signals.get(path);
    return {
      path,
      healthScore: s?.health_score ?? null,
      isHotspot: s ? s.is_hotspot : (hotspotFallback.get(path) ?? null),
      // A test is what tests the code, and a package's `__init__.py` is
      // tested through the modules it gathers: neither needs a test of its own.
      untested: gaps.has(path) && !isTestFile(path, s) && !PACKAGE_INIT.test(path),
    };
  };
}

/** Read one result into the facts every part of the review shows. */
export function readReach(result: Result, changedFiles: string[]): Reach {
  const signals = new Map((result.affected_signals ?? []).map((s) => [s.path, s]));
  const marks = marksReader(result);
  const files: ReachedFile[] = [];
  const tests: string[] = [];
  for (const t of result.transitive_affected) {
    const signal = signals.get(t.path);
    if (isTestFile(t.path, signal)) tests.push(t.path);
    else files.push({ ...marks(t.path), depth: t.depth, via: t.via ?? null, module: moduleOf(t.path, signal) });
  }
  return {
    changed: changedFiles.map(marks).sort(byAttentionThenPath),
    files,
    tests,
    direct: files.filter((f) => f.depth === 1).length,
    modules: new Set(files.map((f) => f.module)).size,
    maxHop: files.reduce((m, f) => Math.max(m, f.depth), 0),
  };
}

/**
 * Files to look at first: changed or one import away, and marked as changed
 * often, weak health or untested. Changed files lead; then the most marks.
 */
export function riskySpots(reach: Reach, limit = 5): (FileMarks & { changed: boolean })[] {
  return [
    ...reach.changed.map((m) => ({ ...m, changed: true })),
    ...reach.files.filter((f) => f.depth === 1).map((f) => ({ ...f, changed: false })),
  ]
    .filter((n) => attention(n) > 0)
    .sort((a, b) => Number(b.changed) - Number(a.changed) || attention(b) - attention(a) || a.path.localeCompare(b.path))
    .slice(0, limit);
}

// ─────────────────────────────────────────────────────────────────────
// The picture
// ─────────────────────────────────────────────────────────────────────

interface MapNode {
  key: string;
  x: number;
  y: number;
  r: number;
  /** The drawn node this one hangs from; `null` hangs from the centre. */
  parentKey: string | null;
}

export interface MapFileNode extends MapNode, FileMarks {
  /** 0 for a changed file. */
  hop: number;
}

export interface MapModuleNode extends MapNode {
  name: string;
  /** The nearest hop any of its files sits at. */
  hop: number;
  files: number;
  /** Files in it that change often and have no test. */
  flagged: number;
  /** Drawn with its name; the smaller ones name themselves on hover. */
  labelled: boolean;
}

export interface BlastMap {
  width: number;
  height: number;
  cx: number;
  cy: number;
  ringRadii: number[];
  mode: "files" | "modules";
  changed: MapFileNode[];
  files: MapFileNode[];
  modules: MapModuleNode[];
  hiddenChanged: number;
  hiddenModules: number;
  hiddenModuleFiles: number;
  maxHop: number;
}

const WIDTH = 640;
const HEIGHT = 480;
export const CX = WIDTH / 2;
export const CY = HEIGHT / 2;
const RINGS = [0, 100, 160, 205];
export const MAX_CHANGED = 8;
/** At or under this many reached files, each is drawn; over it, modules are. */
export const MAX_FILE_NODES = 15;
export const MAX_MODULES = 14;
const LABELLED_MODULES = 8;

function polar(angle: number, radius: number): { x: number; y: number } {
  return { x: CX + Math.cos(angle) * radius, y: CY + Math.sin(angle) * radius };
}

/** Evenly spaced angles from the top, clockwise. */
function spread(n: number, offset = 0): number[] {
  return Array.from({ length: n }, (_, i) => (i / Math.max(1, n)) * 2 * Math.PI - Math.PI / 2 + offset);
}

/** Rings for one hop, two hops, and three or more. */
function ringOf(hop: number): 1 | 2 | 3 {
  return hop <= 1 ? 1 : hop === 2 ? 2 : 3;
}

function centre(reach: Reach): MapFileNode[] {
  const shown = reach.changed.slice(0, MAX_CHANGED);
  const r = shown.length <= 1 ? 0 : Math.min(44, 12 + shown.length * 5);
  const angles = spread(shown.length);
  return shown.map((m, i) => ({ ...m, ...polar(angles[i] ?? 0, r), key: `c:${m.path}`, r: 8, hop: 0, parentKey: null }));
}

/** Each reached file on the ring of its hop, siblings (same `via`) side by side. */
function fileNodes(reach: Reach, changed: MapFileNode[]): MapFileNode[] {
  const keyOf = new Map(changed.map((n) => [n.path, n.key]));
  const out: MapFileNode[] = [];
  for (const ring of [1, 2, 3] as const) {
    const onRing = reach.files
      .filter((f) => ringOf(f.depth) === ring)
      .sort((a, b) => (a.via ?? "").localeCompare(b.via ?? "") || a.path.localeCompare(b.path));
    const angles = spread(onRing.length, 0.15 * ring);
    for (const [i, f] of onRing.entries()) {
      const key = `f:${f.path}`;
      const parentKey = (f.via && keyOf.get(f.via)) || null;
      out.push({ ...f, ...polar(angles[i] ?? 0, RINGS[ring]!), key, r: 6, hop: f.depth, parentKey });
      keyOf.set(f.path, key);
    }
  }
  return out;
}

interface Group {
  name: string;
  files: ReachedFile[];
  hop: number;
}

function groupsOf(reach: Reach): Group[] {
  const byName = new Map<string, Group>();
  for (const f of reach.files) {
    const g = byName.get(f.module) ?? { name: f.module, files: [], hop: f.depth };
    g.files.push(f);
    g.hop = Math.min(g.hop, f.depth);
    byName.set(f.module, g);
  }
  return [...byName.values()].sort((a, b) => b.files.length - a.files.length || a.name.localeCompare(b.name));
}

/** The drawn module most of *g*'s files are reached through, when it sits nearer the change. */
function parentModule(g: Group, moduleOfPath: Map<string, string>, hopOf: Map<string, number>): string | null {
  const votes = new Map<string, number>();
  for (const f of g.files) {
    const m = f.via ? moduleOfPath.get(f.via) : undefined;
    if (m && m !== g.name && (hopOf.get(m) ?? Infinity) < g.hop) votes.set(m, (votes.get(m) ?? 0) + 1);
  }
  return [...votes.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0] ?? null;
}

function moduleNodes(reach: Reach): { nodes: MapModuleNode[]; hidden: Group[] } {
  const groups = groupsOf(reach);
  const shown = groups.slice(0, MAX_MODULES);
  const moduleOfPath = new Map(reach.files.map((f) => [f.path, f.module]));
  const hopOf = new Map(shown.map((g) => [g.name, g.hop]));
  const maxCount = Math.max(1, ...shown.map((g) => g.files.length));
  const labelled = new Set(shown.slice(0, LABELLED_MODULES).map((g) => g.name));
  // Around the ring by hop then name, so modules at one distance sit together.
  const placed = [...shown].sort((a, b) => a.hop - b.hop || a.name.localeCompare(b.name));
  const angles = spread(placed.length, 0.2);
  const nodes = placed.map((g, i): MapModuleNode => {
    const parent = parentModule(g, moduleOfPath, hopOf);
    return {
      key: `m:${g.name}`,
      name: g.name,
      ...polar(angles[i] ?? 0, RINGS[ringOf(g.hop)]!),
      r: 6 + 14 * Math.sqrt(g.files.length / maxCount),
      hop: g.hop,
      files: g.files.length,
      flagged: g.files.filter(needsLook).length,
      labelled: labelled.has(g.name),
      parentKey: parent ? `m:${parent}` : null,
    };
  });
  return { nodes, hidden: groups.slice(MAX_MODULES) };
}

export function buildBlastMap(reach: Reach): BlastMap {
  const changed = centre(reach);
  const mode = reach.files.length <= MAX_FILE_NODES ? "files" : "modules";
  const { nodes, hidden } = mode === "modules" ? moduleNodes(reach) : { nodes: [], hidden: [] as Group[] };
  return {
    width: WIDTH,
    height: HEIGHT,
    cx: CX,
    cy: CY,
    ringRadii: RINGS.slice(1),
    mode,
    changed,
    files: mode === "files" ? fileNodes(reach, changed) : [],
    modules: nodes,
    hiddenChanged: reach.changed.length - changed.length,
    hiddenModules: hidden.length,
    hiddenModuleFiles: hidden.reduce((n, g) => n + g.files.length, 0),
    maxHop: reach.maxHop,
  };
}
