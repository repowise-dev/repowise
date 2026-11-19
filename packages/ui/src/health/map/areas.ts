/**
 * The area hierarchy the map draws: every directory, keyed by path, with the
 * rollup the map colours and labels it by. `""` is the repository root.
 *
 * Two sources fill it. A host with the areas API merges `/areas/tree`
 * responses in as the reader zooms ({@link mergeAreaTree}); the rollups are
 * the server's, complete over the repository. A host with only a flat file
 * feed derives the same hierarchy from those rows ({@link deriveAreaIndex}),
 * so every host gets the same nested map from one code path.
 *
 * Pure: no React, no canvas, no fetch.
 */

import { bandForScore, type HealthBand } from "@repowise-dev/types/health";
import type { AreaNode, AreaTree } from "@repowise-dev/types/areas";
import { ROOT_AREA, dirOf } from "../../lib/areas";
import type { CodeHealthMapFile } from "./types";


/** The rollup of one directory, in the fields the map reads. */
export interface MapArea {
  path: string;
  /** Last path segment; `""` for the root. */
  name: string;
  parent: string | null;
  fileCount: number;
  /** Files directly in this directory. */
  directFileCount: number;
  nloc: number;
  hasChildren: boolean;
  /** NLOC-weighted score, `null` when no file in it is scored. */
  health: number | null;
  band: HealthBand | null;
  /** `undefined` when the source does not carry the signal at all. */
  hotspotCount?: number;
  busFactor1Count?: number;
  primaryOwner?: { key: string; name: string; share: number } | null;
  coveragePct?: number | null;
  delta30d?: number | null;
}

export interface AreaIndex {
  areas: ReadonlyMap<string, MapArea>;
  /** Child paths per area, biggest first. Complete only for `expanded` areas. */
  children: ReadonlyMap<string, readonly string[]>;
  /** Areas whose child list is complete. */
  expanded: ReadonlySet<string>;
}

export const EMPTY_AREA_INDEX: AreaIndex = {
  areas: new Map(),
  children: new Map(),
  expanded: new Set(),
};

export function areaFromNode(n: AreaNode): MapArea {
  return {
    path: n.path,
    name: n.name,
    parent: n.parent,
    fileCount: n.file_count,
    directFileCount: n.direct_file_count,
    nloc: n.nloc,
    hasChildren: n.has_children,
    health: n.health,
    band: n.band,
    hotspotCount: n.hotspot_count,
    busFactor1Count: n.bus_factor_1_count,
    primaryOwner: n.primary_owner
      ? { key: n.primary_owner.key, name: n.primary_owner.name, share: n.primary_owner.share }
      : null,
    coveragePct: n.line_coverage_pct,
    delta30d: n.health_delta_30d,
  };
}

const bySize = (areas: ReadonlyMap<string, MapArea>) => (a: string, b: string) =>
  (areas.get(b)?.nloc ?? 0) - (areas.get(a)?.nloc ?? 0) || (a < b ? -1 : 1);

function indexChildren(areas: Map<string, MapArea>): Map<string, string[]> {
  const children = new Map<string, string[]>();
  for (const a of areas.values()) {
    if (a.parent === null) continue;
    const list = children.get(a.parent);
    if (list) list.push(a.path);
    else children.set(a.parent, [a.path]);
  }
  const order = bySize(areas);
  for (const list of children.values()) list.sort(order);
  return children;
}

/**
 * Fold one `/areas/tree` response in. Every area above the response's last
 * level arrives with all of its children, so those become expanded; a leaf
 * the server says has no children is expanded trivially.
 */
export function mergeAreaTree(index: AreaIndex, tree: AreaTree): AreaIndex {
  const areas = new Map(index.areas);
  const expanded = new Set(index.expanded);
  const deepest = tree.root.depth + tree.depth;
  for (const n of [tree.root, ...tree.nodes]) {
    areas.set(n.path, areaFromNode(n));
    if (n.depth < deepest || !n.has_children) expanded.add(n.path);
  }
  return { areas, children: indexChildren(areas), expanded };
}

function lastSegment(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** Every ancestor directory of `dir`, root first, ending at `dir`. */
function ancestry(dir: string): string[] {
  const out = [ROOT_AREA];
  if (!dir) return out;
  const parts = dir.split("/");
  for (let i = 1; i <= parts.length; i++) out.push(parts.slice(0, i).join("/"));
  return out;
}

interface Acc {
  files: number;
  direct: number;
  nloc: number;
  scoredNloc: number;
  weighted: number;
  scored: number;
}

/**
 * The hierarchy behind a flat file feed, for a host without the areas API.
 * The same fold the server uses: every file counts, an empty one included,
 * and health is the mean of the scored files weighted by NLOC floored at 1.
 * Every directory is expanded because every file is here.
 */
export function deriveAreaIndex(files: readonly CodeHealthMapFile[]): AreaIndex {
  const acc = new Map<string, Acc>();
  const touch = (path: string) => {
    let a = acc.get(path);
    if (!a) {
      a = { files: 0, direct: 0, nloc: 0, scoredNloc: 0, weighted: 0, scored: 0 };
      acc.set(path, a);
    }
    return a;
  };
  for (const f of files) {
    const nloc = Math.max(f.nloc, 0);
    const weight = Math.max(nloc, 1);
    const dir = dirOf(f.file_path);
    const chain = ancestry(dir);
    for (const p of chain) {
      const a = touch(p);
      a.files += 1;
      a.nloc += nloc;
      if (f.score != null) {
        a.scored += 1;
        a.scoredNloc += weight;
        a.weighted += f.score * weight;
      }
    }
    touch(dir).direct += 1;
  }
  const areas = new Map<string, MapArea>();
  for (const [path, a] of acc) {
    const health = a.scoredNloc > 0 ? Math.round((a.weighted / a.scoredNloc) * 100) / 100 : null;
    areas.set(path, {
      path,
      name: path ? lastSegment(path) : "",
      parent: path ? dirOf(path) : null,
      fileCount: a.files,
      directFileCount: a.direct,
      nloc: a.nloc,
      hasChildren: false,
      health,
      band: health == null ? null : bandForScore(health),
    });
  }
  const children = indexChildren(areas);
  for (const [path, list] of children) {
    const a = areas.get(path);
    if (a && list.length) areas.set(path, { ...a, hasChildren: true });
  }
  return { areas, children, expanded: new Set(areas.keys()) };
}
