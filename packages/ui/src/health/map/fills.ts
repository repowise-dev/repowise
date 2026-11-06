/**
 * Every area's and every file's colour under one lens, as pure functions.
 *
 * This is the one place "not analysed" is decided, for every lens. A file
 * the lens has no signal for is grey, whatever its other fields say. An area
 * takes its rollup's reading when the lens has one, else the fold of its
 * loaded files the lens analysed, else grey: a folder whose files are not
 * loaded, or none of which were analysed, is unknown, not good and not bad.
 */

import { ROOT_AREA, dirOf } from "../../lib/areas";
import type { AreaIndex } from "./areas";
import type { FieldScene } from "./paint";
import { NEUTRAL_FILL, type MapLens } from "./lens";
import type { CodeHealthMapFile } from "./types";

/** How many of an area's loaded files the lens analysed. */
export interface AreaAnalysed {
  analysed: number;
  loaded: number;
  /** Analysed files carrying something to fix, for a lens that flags. */
  flagged?: number;
}

export interface AreaFills {
  fill: Map<string, string>;
  /** The lens's figure per area, from its rollup and its loaded, analysed files. */
  text: Map<string, string | null>;
  /** Per area with loaded files, for a lens that tells files apart; else empty. */
  analysed: Map<string, AreaAnalysed>;
}

const isAnalysed = (lens: MapLens, f: CodeHealthMapFile) => !lens.analysed || lens.analysed(f);

/** Every ancestor area of a file the index knows, deepest first. */
function areasOf(index: AreaIndex, path: string): string[] {
  const out: string[] = [];
  let dir = dirOf(path);
  for (;;) {
    if (index.areas.has(dir)) out.push(dir);
    if (dir === ROOT_AREA) return out;
    dir = dirOf(dir);
  }
}

/** Loaded files under every area, split into the analysed ones and a count of all. */
function foldInputs(index: AreaIndex, lens: MapLens, files: readonly CodeHealthMapFile[]) {
  const analysedFiles = new Map<string, CodeHealthMapFile[]>();
  const counts = new Map<string, AreaAnalysed>();
  for (const f of files) {
    const ok = isAnalysed(lens, f);
    for (const area of areasOf(index, f.file_path)) {
      const c = counts.get(area) ?? { analysed: 0, loaded: 0 };
      c.loaded += 1;
      if (ok) {
        c.analysed += 1;
        if (lens.flagged) c.flagged = (c.flagged ?? 0) + (lens.flagged(f) ? 1 : 0);
        const list = analysedFiles.get(area);
        if (list) list.push(f);
        else analysedFiles.set(area, [f]);
      }
      counts.set(area, c);
    }
  }
  return { analysedFiles, counts };
}

export function areaFills(index: AreaIndex, lens: MapLens, files: readonly CodeHealthMapFile[]): AreaFills {
  const fill = new Map<string, string>();
  const text = new Map<string, string | null>();
  const inputs = files.length > 0 ? foldInputs(index, lens, files) : null;
  for (const a of index.areas.values()) {
    const fromRollup = lens.areaFill?.(a) ?? null;
    const analysed = inputs?.analysedFiles.get(a.path);
    text.set(a.path, lens.areaText?.(a, analysed) ?? null);
    if (fromRollup !== null) fill.set(a.path, fromRollup);
    else if (analysed?.length && lens.areaFromFiles) fill.set(a.path, lens.areaFromFiles(analysed));
    else fill.set(a.path, NEUTRAL_FILL);
  }
  return { fill, text, analysed: lens.analysed && inputs ? inputs.counts : new Map() };
}

export function fileFills(scene: FieldScene, lens: MapLens, areaFill: ReadonlyMap<string, string>): string[] {
  return scene.nodes.map((n, i) => {
    if (!isAnalysed(lens, n.file)) return NEUTRAL_FILL;
    if (lens.fill) return lens.fill(n.file);
    const host = scene.hosts[scene.hostOfNode[i]!];
    return (host && areaFill.get(host.area)) ?? NEUTRAL_FILL;
  });
}
