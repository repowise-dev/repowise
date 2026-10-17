"use client";

/**
 * The field as data: laid out once per feed, never per resize or lens, plus
 * the lens's fills and the search marks over it. Matching runs over the drawn
 * field only and produces a bounded list for the marks layer; neither the
 * layout nor the fills see the query, so typing repaints rings and nothing
 * else.
 */

import { useMemo } from "react";

import { isLoudFill } from "../tokens";
import { buildHitGrid } from "./hit-test";
import { layoutField } from "./layout";
import type { OverlaySpec } from "./lens";
import { SEARCH_MARK_CAP } from "./overlay";
import { buildScene } from "./paint";
import type { CodeHealthMapFile } from "./types";

export function useFieldModel(
  files: CodeHealthMapFile[],
  spec: OverlaySpec,
  search: string | undefined,
  highlightPaths: string[] | undefined,
) {
  const galaxies = useMemo(() => layoutField(files), [files]);
  const scene = useMemo(() => buildScene(galaxies), [galaxies]);
  const grid = useMemo(() => buildHitGrid(scene.nodes), [scene]);
  const minNodeR = useMemo(
    () => scene.nodes.reduce((m, n) => Math.min(m, n.r), Infinity),
    [scene],
  );
  // Galaxies biggest first, the order labels claim space in.
  const labelOrder = useMemo(
    () => galaxies.map((_, i) => i).sort((a, b) => galaxies[b]!.R - galaxies[a]!.R),
    [galaxies],
  );
  const fills = useMemo(() => scene.nodes.map((n) => spec.fill(n.file)), [scene, spec]);
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

  return { galaxies, scene, grid, minNodeR, labelOrder, fills, attention, query, matches, marked };
}
