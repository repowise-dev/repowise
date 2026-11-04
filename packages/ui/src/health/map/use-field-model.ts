"use client";

/**
 * The field as data: the area discs laid out once per hierarchy, the loaded
 * files placed in them once per feed, and the lens's colours over both. None
 * of it depends on the camera or the canvas size, and the search marks are
 * computed apart from the fills, so typing repaints rings and nothing else.
 */

import { useMemo } from "react";

import { isLoudFill } from "../tokens";
import type { AreaIndex } from "./areas";
import { layoutAreas, layoutFiles } from "./area-layout";
import { areaFills, fileFills } from "./fills";
import { buildHitGrid } from "./hit-test";
import type { MapLens } from "./lens";
import { SEARCH_MARK_CAP } from "./overlay";
import { buildScene } from "./paint";
import type { CodeHealthMapFile } from "./types";

export function useFieldModel(
  index: AreaIndex,
  files: readonly CodeHealthMapFile[],
  lens: MapLens,
  search: string | undefined,
  highlightPaths: string[] | undefined,
) {
  const geo = useMemo(() => layoutAreas(index), [index]);
  const placed = useMemo(() => layoutFiles(files, index, geo), [files, index, geo]);
  const scene = useMemo(() => buildScene(placed), [placed]);
  const grid = useMemo(() => buildHitGrid(scene.nodes), [scene]);
  const minNodeR = useMemo(
    () => scene.nodes.reduce((m, n) => Math.min(m, n.r), Infinity),
    [scene],
  );
  const fold = useMemo(() => areaFills(index, lens, files), [index, lens, files]);
  const areaFill = fold.fill;
  const fills = useMemo(() => fileFills(scene, lens, areaFill), [scene, lens, areaFill]);
  const attention = useMemo(() => fills.filter(isLoudFill).length, [fills]);

  const query = (search ?? "").trim().toLowerCase();
  const matches = useMemo(() => {
    if (query.length === 0) return [] as number[];
    const out: number[] = [];
    scene.nodes.forEach((n, i) => {
      if (n.file.file_path.toLowerCase().includes(query)) out.push(i);
    });
    return out;
  }, [query, scene]);
  const marked = useMemo(() => {
    const extra = (highlightPaths ?? [])
      .map((p) => scene.index.get(p))
      .filter((i): i is number => i !== undefined);
    const all = extra.length ? [...new Set([...extra, ...matches])] : matches;
    return all.slice(0, SEARCH_MARK_CAP);
  }, [highlightPaths, matches, scene]);

  return {
    geo,
    scene,
    grid,
    minNodeR,
    areaFill,
    areaAnalysed: fold.analysed,
    areaText: fold.text,
    fills,
    attention,
    unplaced: placed.unplaced,
    query,
    matches,
    marked,
  };
}

export type FieldModel = ReturnType<typeof useFieldModel>;
