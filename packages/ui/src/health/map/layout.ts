/**
 * Packing files into a disc, and the world square the map lives in.
 *
 * Deterministic on purpose. A reader builds spatial memory of this field, and
 * that memory is only worth having if the same repository lays out the same
 * way every time, under every lens. Nothing here reads a lens or the canvas
 * size: the field is laid out once into a fixed world square and the camera
 * fits that square to whatever box it is given.
 *
 * Where the discs go is `area-layout.ts`; this module fills one disc with the
 * files it hosts, packed tight within each folder, biggest first, so related
 * files sit side by side and nothing overlaps.
 */

import { packEnclose, packSiblings } from "d3-hierarchy";
import { dirOf } from "../../lib/areas";
import type { CodeHealthMapFile } from "./types";

/** Side of the world square the field is laid out in. */
export const WORLD = 1000;

/** Lines below which a file is drawn at the floor size, so it stays a dot. */
const NLOC_FLOOR = 4;
/** Air around each dot, in floor radii, so neighbours never touch. */
const DOT_GAP = 0.45;
/** Air around each folder cluster, in floor radii, so folders read apart. */
const FOLDER_GAP = 1.6;
const FLOOR_R = Math.sqrt(NLOC_FLOOR);

export const byNlocThenPath = (a: CodeHealthMapFile, b: CodeHealthMapFile) =>
  b.nloc - a.nloc || (a.file_path < b.file_path ? -1 : 1);

/** A file placed relative to its cluster's centre, before scaling. */
export interface LocalNode {
  file: CodeHealthMapFile;
  x: number;
  y: number;
  r: number;
}

/**
 * Pack one disc's files: tight within each folder, then the folders packed
 * together. `radius` maps lines to a radius; the result is centred on 0,0 and
 * `R` is the smallest circle that holds it.
 */
export function packCluster(
  files: readonly CodeHealthMapFile[],
  radius: (nloc: number) => number = fileRadius,
): { nodes: LocalNode[]; R: number } {
  const dotPad = FLOOR_R * DOT_GAP;
  const byDir = new Map<string, CodeHealthMapFile[]>();
  for (const f of [...files].sort(byNlocThenPath)) {
    const d = dirOf(f.file_path);
    const arr = byDir.get(d);
    if (arr) arr.push(f);
    else byDir.set(d, [f]);
  }
  const clusters = [...byDir.entries()].map(([dir, group]) => {
    const dots = packSiblings(group.map((file) => ({ file, r: radius(file.nloc) + dotPad })));
    const enc = packEnclose(dots) ?? { x: 0, y: 0, r: dotPad };
    return { dir, dots, ex: enc.x, ey: enc.y, r: enc.r + (byDir.size > 1 ? FLOOR_R * FOLDER_GAP : 0) };
  });
  clusters.sort((a, b) => b.r - a.r || (a.dir < b.dir ? -1 : 1));
  const placed = packSiblings(clusters);
  const enc = packEnclose(placed) ?? { x: 0, y: 0, r: FLOOR_R };
  const nodes: LocalNode[] = [];
  for (const c of placed) {
    for (const d of c.dots) {
      nodes.push({
        file: d.file,
        x: c.x - enc.x + (d.x - c.ex),
        y: c.y - enc.y + (d.y - c.ey),
        r: d.r - dotPad,
      });
    }
  }
  return { nodes, R: enc.r + FLOOR_R };
}

/** A file's radius before scaling: area is lines, floored so a tiny file is a dot. */
export function fileRadius(nloc: number): number {
  return Math.sqrt(Math.max(NLOC_FLOOR, nloc));
}

/** The camera that shows the whole world square in a `w` by `h` box. */
export function fitCamera(w: number, h: number): { cx: number; cy: number; scale: number } {
  return { cx: WORLD / 2, cy: WORLD / 2, scale: (Math.max(1, Math.min(w, h)) * 0.94) / WORLD };
}
