/**
 * The blast radius as a picture: the change at the centre, the files one hop
 * out drawn one by one, and everything further out grouped by module on the
 * rings beyond. Pure: the same result always lays out the same way.
 *
 * Why modules past the first hop: on a real index one hop is a dozen files and
 * two hops is hundreds, so per-file dots past the first ring are a hairball
 * that says nothing. A module bubble keeps the count and the risk marks.
 */

import type {
  AffectedSignal,
  BlastRadiusResponse,
} from "@repowise-dev/types/blast-radius";
import { HEALTH_BAND_LABEL, formatScore, isWeakHealthBand, bandForScore } from "@repowise-dev/types/health";

/** What the index knows about a file. `null` is unknown, never "fine". */
export interface FileMarks {
  path: string;
  healthScore: number | null;
  isHotspot: boolean | null;
  /** Listed by the analysis as having no test evidence at all. */
  untested: boolean;
}

export interface MapFileNode extends FileMarks {
  key: string;
  x: number;
  y: number;
  r: number;
  hop: 0 | 1;
  /** The drawn node this one hangs from, `null` for the centre. */
  parentKey: string | null;
}

export interface MapModuleNode {
  key: string;
  name: string;
  x: number;
  y: number;
  r: number;
  /** The nearest hop any of its files sits at. */
  hop: number;
  files: FileMarks[];
  hotspots: number;
  weak: number;
  untested: number;
  parentKey: string | null;
}

export interface BlastMap {
  width: number;
  height: number;
  cx: number;
  cy: number;
  ringRadii: number[];
  changed: MapFileNode[];
  firstHop: MapFileNode[];
  modules: MapModuleNode[];
  /** Counts of what exists but is not drawn, for the caption. */
  hiddenChanged: number;
  hiddenFirstHop: number;
  hiddenModules: number;
  hiddenModuleFiles: number;
  /** Files reached past the first hop, drawn or not. */
  furtherFiles: number;
  /** Every reached file, every module they fall in. */
  reachedFiles: number;
  reachedModules: number;
  maxHop: number;
}

const WIDTH = 760;
const HEIGHT = 560;
export const CX = WIDTH / 2;
export const CY = HEIGHT / 2;
const RINGS = [0, 128, 200, 248];
export const MAX_CHANGED = 8;
export const MAX_FIRST_HOP = 18;
export const MAX_MODULES = 14;

/** A file's module: the index's own when health scored it, else its folder two levels down. */
export function moduleOf(path: string, signal: AffectedSignal | undefined): string {
  if (signal?.module) return signal.module;
  const parts = path.split("/");
  if (parts.length <= 1) return "(root)";
  return parts.slice(0, Math.min(2, parts.length - 1)).join("/");
}

/** How much a file asks for attention: hotspot, weak health, no tests. */
export function attention(m: FileMarks): number {
  return (
    (m.isHotspot ? 1 : 0) +
    (m.healthScore !== null && isWeakHealthBand(bandForScore(m.healthScore)) ? 1 : 0) +
    (m.untested ? 1 : 0)
  );
}

/** Every mark a file carries, in words, for tooltips, lists and the review prompt. */
export function describeMarks(m: FileMarks): string[] {
  const words: string[] = [];
  if (m.healthScore !== null)
    words.push(`${HEALTH_BAND_LABEL[bandForScore(m.healthScore)]} ${formatScore(m.healthScore)}`);
  else words.push("Health not scored");
  if (m.isHotspot) words.push("Hotspot");
  if (m.untested) words.push("No test evidence");
  return words;
}

export function isWeak(m: FileMarks): boolean {
  return m.healthScore !== null && isWeakHealthBand(bandForScore(m.healthScore));
}

function marksFor(
  path: string,
  signals: Map<string, AffectedSignal>,
  gaps: Set<string>,
  hotspotFallback: Map<string, boolean>,
): FileMarks {
  const s = signals.get(path);
  return {
    path,
    healthScore: s?.health_score ?? null,
    isHotspot: s ? s.is_hotspot : (hotspotFallback.get(path) ?? null),
    untested: gaps.has(path),
  };
}

function polar(angle: number, radius: number): { x: number; y: number } {
  return { x: CX + Math.cos(angle) * radius, y: CY + Math.sin(angle) * radius };
}

/** Evenly spaced angles from the top, clockwise. */
function spread(n: number, offset = 0): number[] {
  return Array.from({ length: n }, (_, i) => (i / Math.max(1, n)) * 2 * Math.PI - Math.PI / 2 + offset);
}

function byAttentionThenPath(a: FileMarks, b: FileMarks): number {
  return attention(b) - attention(a) || a.path.localeCompare(b.path);
}

export function buildBlastMap(
  result: BlastRadiusResponse & { affected_signals?: AffectedSignal[] },
  changedFiles: string[],
): BlastMap {
  const signals = new Map((result.affected_signals ?? []).map((s) => [s.path, s]));
  const gaps = new Set(result.test_gaps);
  // Without signals (an older server, a manual file pick there) the changed
  // files still carry the analysis's own hotspot verdict.
  const hotspotFallback = new Map(result.direct_risks.map((d) => [d.path, d.is_hotspot]));
  const marks = (p: string) => marksFor(p, signals, gaps, hotspotFallback);

  // Centre: the changed files, the riskiest first when there are too many.
  const changedAll = changedFiles.map(marks).sort(byAttentionThenPath);
  const changedShown = changedAll.slice(0, MAX_CHANGED);
  const centreR = changedShown.length <= 1 ? 0 : Math.min(56, 16 + changedShown.length * 6);
  const changed: MapFileNode[] = changedShown.map((m, i, arr) => {
    const { x, y } = polar(spread(arr.length)[i] ?? 0, centreR);
    return { ...m, key: `c:${m.path}`, x, y, r: 9, hop: 0, parentKey: null };
  });
  const changedKey = new Map(changed.map((n) => [n.path, n.key]));

  // First hop: one node per file, siblings (same `via`) next to each other.
  const firstAll = result.transitive_affected.filter((t) => t.depth === 1);
  const firstRanked = firstAll
    .map((t) => ({ t, m: marks(t.path) }))
    .sort((a, b) => byAttentionThenPath(a.m, b.m))
    .slice(0, MAX_FIRST_HOP)
    .sort((a, b) => (a.t.via ?? "").localeCompare(b.t.via ?? "") || a.t.path.localeCompare(b.t.path));
  const firstAngles = spread(firstRanked.length, 0.12);
  const firstHop: MapFileNode[] = firstRanked.map(({ t, m }, i) => {
    const { x, y } = polar(firstAngles[i] ?? 0, RINGS[1]!);
    return {
      ...m,
      key: `h1:${t.path}`,
      x,
      y,
      r: 6,
      hop: 1,
      parentKey: (t.via && changedKey.get(t.via)) ?? null,
    };
  });
  const firstKey = new Map(firstHop.map((n) => [n.path, n]));

  // Further out: grouped by module, at the ring of the nearest member.
  const further = result.transitive_affected.filter((t) => t.depth > 1);
  type Group = { files: FileMarks[]; hop: number; via: Map<string, number> };
  const groups = new Map<string, Group>();
  for (const t of further) {
    const name = moduleOf(t.path, signals.get(t.path));
    const g: Group = groups.get(name) ?? { files: [], hop: t.depth, via: new Map() };
    g.files.push(marks(t.path));
    g.hop = Math.min(g.hop, t.depth);
    if (t.via) g.via.set(t.via, (g.via.get(t.via) ?? 0) + 1);
    groups.set(name, g);
  }
  const groupList = [...groups.entries()]
    .map(([name, g]) => {
      // The drawn first-hop file most of this module is reached through.
      const parent = [...g.via.entries()]
        .filter(([p]) => firstKey.has(p))
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0];
      const parentNode = parent ? firstKey.get(parent) : undefined;
      return {
        name,
        g,
        parentNode,
        angle: parentNode ? Math.atan2(parentNode.y - CY, parentNode.x - CX) : Math.PI,
      };
    })
    .sort((a, b) => b.g.files.length - a.g.files.length || a.name.localeCompare(b.name));
  const shownGroups = groupList.slice(0, MAX_MODULES).sort((a, b) => {
    // Lay modules out around the ring in their parents' order, so edges fan
    // out instead of crossing the picture.
    const norm = (v: number) => (v + Math.PI / 2 + 2 * Math.PI) % (2 * Math.PI);
    return norm(a.angle) - norm(b.angle) || a.name.localeCompare(b.name);
  });
  const moduleAngles = spread(shownGroups.length, 0.2);
  const maxCount = Math.max(1, ...shownGroups.map((s) => s.g.files.length));
  const modules: MapModuleNode[] = shownGroups.map(({ name, g, parentNode }, i) => {
    const ring = RINGS[Math.min(g.hop, 3)]!;
    const { x, y } = polar(moduleAngles[i] ?? 0, ring);
    return {
      key: `m:${name}`,
      name,
      x,
      y,
      r: 7 + 13 * Math.sqrt(g.files.length / maxCount),
      hop: g.hop,
      files: [...g.files].sort(byAttentionThenPath),
      hotspots: g.files.filter((f) => f.isHotspot).length,
      weak: g.files.filter(isWeak).length,
      untested: g.files.filter((f) => f.untested).length,
      parentKey: parentNode?.key ?? null,
    };
  });

  const reachedModules = new Set(
    result.transitive_affected.map((t) => moduleOf(t.path, signals.get(t.path))),
  ).size;
  return {
    width: WIDTH,
    height: HEIGHT,
    cx: CX,
    cy: CY,
    ringRadii: RINGS.slice(1),
    changed,
    firstHop,
    modules,
    hiddenChanged: changedAll.length - changed.length,
    hiddenFirstHop: firstAll.length - firstHop.length,
    hiddenModules: groupList.length - modules.length,
    hiddenModuleFiles: groupList.slice(MAX_MODULES).reduce((n, s) => n + s.g.files.length, 0),
    furtherFiles: further.length,
    reachedFiles: result.transitive_affected.length,
    reachedModules,
    maxHop: result.transitive_affected.reduce((m, t) => Math.max(m, t.depth), 0),
  };
}

/**
 * Files to look at first: changed or one hop out, and marked as a hotspot,
 * weak health or untested. Changed files lead; then the most marks.
 */
export function riskySpots(map: BlastMap, limit = 5): MapFileNode[] {
  return [...map.changed, ...map.firstHop]
    .filter((n) => attention(n) > 0)
    .sort((a, b) => a.hop - b.hop || attention(b) - attention(a) || a.path.localeCompare(b.path))
    .slice(0, limit);
}
