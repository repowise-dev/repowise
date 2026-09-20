/**
 * Where every node sits, as one pure function of the rows and the canvas size.
 *
 * Deterministic on purpose. A reader builds spatial memory of this field, and
 * that memory is only worth having if the same repository lays out the same
 * way every time, under every lens. Nothing here reads a lens.
 */

import * as d3 from "d3-hierarchy";
import type { CodeHealthMapFile, Galaxy, GalaxyAgg } from "./types";

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5)); // ~2.39996 rad

/**
 * Group flat file rows into per-module galaxies, dropping zero-NLOC files
 * (they cannot be sized). Files come back sorted biggest-first.
 */
export function groupByModule(files: CodeHealthMapFile[]): GalaxyAgg[] {
  const byModule = new Map<string, CodeHealthMapFile[]>();
  for (const f of files) {
    if (f.nloc <= 0) continue;
    const key = f.module ?? "(ungrouped)";
    const arr = byModule.get(key);
    if (arr) arr.push(f);
    else byModule.set(key, [f]);
  }
  const out: GalaxyAgg[] = [];
  for (const [module, group] of byModule) {
    group.sort((a, b) => b.nloc - a.nloc);
    out.push({
      module,
      files: group,
      totalNloc: group.reduce((s, f) => s + f.nloc, 0),
      maxNloc: group[0]?.nloc ?? 1,
    });
  }
  // Biggest galaxies first -> stable z-order (small ones paint on top).
  out.sort((a, b) => b.totalNloc - a.totalNloc);
  return out;
}

/** Stable, well-spread hash so a module maps to a consistent palette family. */
export function moduleColorId(module: string): number {
  let h = 0;
  for (let i = 0; i < module.length; i++) h = (h * 31 + module.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/**
 * Galaxy placement (d3-pack) plus hub-and-spoke file layout (phyllotaxis).
 *
 * `S` is the square the field is packed into; the caller centres it in the
 * container. Returns an empty field for an empty input rather than throwing,
 * because a host renders before its data lands.
 */
export function packGalaxies(files: CodeHealthMapFile[], S: number): Galaxy[] {
  if (S === 0 || files.length === 0) return [];
  const aggs = groupByModule(files);
  const root = d3
    .hierarchy<{ value?: number; agg?: GalaxyAgg; children?: unknown[] }>(
      { children: aggs.map((a) => ({ value: a.totalNloc, agg: a })) },
      (d) => d.children as { value?: number; agg?: GalaxyAgg }[] | undefined,
    )
    .sum((d) => d.value ?? 0);
  d3.pack<{ value?: number; agg?: GalaxyAgg }>().size([S, S]).padding(S * 0.012)(
    root as d3.HierarchyNode<{ value?: number; agg?: GalaxyAgg }>,
  );
  const galaxies: Galaxy[] = [];
  for (const leaf of root.leaves() as d3.HierarchyCircularNode<{ agg?: GalaxyAgg }>[]) {
    const agg = leaf.data.agg;
    if (!agg) continue;
    const R = leaf.r;
    const cx = leaf.x;
    const cy = leaf.y;
    const n = agg.files.length;
    const spread = R * 0.84;
    const maxNodeR = Math.max(2, R * 0.16);
    galaxies.push({
      module: agg.module,
      cx,
      cy,
      R,
      fileCount: n,
      colorId: moduleColorId(agg.module),
      nodes: agg.files.map((file, i) => {
        const rr = spread * Math.sqrt((i + 0.55) / n);
        const ang = i * GOLDEN_ANGLE;
        const nodeR = Math.max(
          1.2,
          Math.min(maxNodeR, maxNodeR * Math.sqrt(file.nloc / agg.maxNloc)),
        );
        return { file, x: cx + rr * Math.cos(ang), y: cy + rr * Math.sin(ang), r: nodeR };
      }),
    });
  }
  return galaxies;
}

/** Deterministic pseudo-random in [0,1) for the static starfield. */
export function rand(i: number): number {
  const x = Math.sin(i * 127.1) * 43758.5453;
  return x - Math.floor(x);
}
