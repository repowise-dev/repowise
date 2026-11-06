"use client";

/**
 * What sits over the field besides the marks: the scope sentence in the
 * corner, the notice for a field nothing scored, and the hover card for the
 * file or area under the pointer.
 */

import { useMemo } from "react";
import type { AreaIndex } from "./areas";
import { displayLabel, displayParent, areaTrail } from "./area-paths";
import { truncatePath } from "../../lib/format";
import type { AreaGeometry } from "./area-layout";
import { HEALTH_UNSUPPORTED_NOTICE, noFileScored, type MapLens } from "./lens";
import type { AreaLabel } from "./labels";
import { AreaHoverCard, HoverCard } from "./overlay";
import { LoadedNote, MapScopeNote, type HeldGroups } from "./scope-note";
import type { CodeHealthMapFile, CodeHealthOverlay, MapScope } from "./types";
import type { Hover } from "./use-map-pointer";
import type { AreaAnalysed } from "./fills";
import type { MapCanvasLabels } from "./canvas-labels";
import { ROOT_AREA } from "../../lib/areas";

/** A sub-folder at least this share of its parent's radius visually is the parent. */
const DOMINANT_SHARE = 0.75;
/** Longest merged name, before the shared path shortening keeps its tail. */
const MERGED_NAME_CHARS = 32;

/**
 * One label per visual disc. When one sub-folder fills its parent, the
 * parent's label is dropped while open and the sub-folder's label carries the
 * joined name ("core/src/repowise/core"), shortened from the front if long.
 * Decided from the layout, so no frame pays for it.
 */
export function mergeDominantLabels(
  index: AreaIndex,
  geo: AreaGeometry,
  areaText: ReadonlyMap<string, string | null>,
  rootLabel: string,
): AreaLabel[] {
  const dominated = new Map<string, string>();
  for (const [path, c] of geo.circles) {
    const parent = displayParent(index, path);
    const pc = parent ? geo.circles.get(parent) : undefined;
    if (parent && pc && c.r >= pc.r * DOMINANT_SHARE) dominated.set(parent, path);
  }
  const nameOf = (path: string): string => {
    const own = displayLabel(index, path, rootLabel);
    const parent = displayParent(index, path);
    return parent && parent !== ROOT_AREA && dominated.get(parent) === path
      ? truncatePath(`${nameOf(parent)}/${own}`, MERGED_NAME_CHARS)
      : own;
  };
  return [...geo.circles.keys()]
    .filter((p) => p !== ROOT_AREA)
    .map((path) => ({
      path,
      name: nameOf(path),
      text: areaText.get(path) ?? null,
      dominated: dominated.has(path),
    }));
}

/** The area names the canvas places and the breadcrumb above it. */
export function useAreaChrome(
  index: AreaIndex,
  geo: AreaGeometry,
  areaText: ReadonlyMap<string, string | null>,
  selected: string,
  rootLabel: string,
  onSelectArea: (path: string) => void,
) {
  const labels = useMemo<AreaLabel[]>(() => mergeDominantLabels(index, geo, areaText, rootLabel), [
    geo,
    index,
    areaText,
    rootLabel,
  ]);
  const trail = useMemo(
    () =>
      areaTrail(index, selected).map((path, i, all) => ({
        label: displayLabel(index, path, rootLabel),
        ...(i < all.length - 1 ? { onSelect: () => onSelectArea(path) } : {}),
      })),
    [index, selected, rootLabel, onSelectArea],
  );
  return { labels, trail };
}

export interface FlatFieldNotes {
  files: CodeHealthMapFile[];
  scope: MapScope | undefined;
  drawMoreStep: number;
  pending: boolean;
  onDrawMore?: (() => void) | undefined;
  onIncludeHeldChange?: ((include: boolean) => void) | undefined;
  onFindUndrawn?: ((query: string) => void) | undefined;
}

/** The scope sentence for a flat feed or a lazily loaded one, and the unscored notice. */
export function FieldNotes({
  flat,
  loaded,
  matches,
  query,
  held,
  labels,
}: {
  flat: FlatFieldNotes | null;
  loaded: { loaded: number; total: number } | null;
  matches: number;
  query: string;
  held?: HeldGroups | undefined;
  labels: MapCanvasLabels;
}) {
  return (
    <>
      {flat?.scope ? (
        <MapScopeNote
          scope={flat.scope}
          matches={matches}
          query={query}
          drawMoreStep={flat.drawMoreStep}
          pending={flat.pending}
          onDrawMore={flat.onDrawMore}
          onIncludeHeldChange={flat.onIncludeHeldChange}
          onFindUndrawn={flat.onFindUndrawn}
        />
      ) : loaded ? (
        <LoadedNote
          loaded={loaded.loaded}
          total={loaded.total}
          matches={query ? matches : null}
          held={held}
          labels={labels}
        />
      ) : null}
      {flat && noFileScored(flat.files) ? (
        <div
          role="status"
          data-testid="map-unanalysed"
          className="pointer-events-none absolute bottom-3 left-3 max-w-[64%] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-2.5 py-1.5 text-xs text-[var(--color-text-secondary)] shadow-sm backdrop-blur-sm sm:max-w-[44%]"
        >
          {HEALTH_UNSUPPORTED_NOTICE}
        </div>
      ) : null}
    </>
  );
}

/** The card for whatever the pointer is over, in the active lens's terms. */
export function FieldHover({
  hover,
  analysed,
  areaText,
  labels,
  index,
  lens,
  overlay,
  rootLabel,
  width,
  height,
}: {
  hover: Hover | null;
  /** How many of each area's loaded files the active lens analysed. */
  analysed: ReadonlyMap<string, AreaAnalysed>;
  areaText: ReadonlyMap<string, string | null>;
  labels: MapCanvasLabels;
  index: AreaIndex;
  lens: MapLens;
  overlay: CodeHealthOverlay;
  rootLabel: string;
  width: number;
  height: number;
}) {
  if (hover?.kind === "file") {
    return <HoverCard file={hover.file} overlay={overlay} x={hover.x} y={hover.y} width={width} height={height} />;
  }
  const area = hover?.kind === "area" ? index.areas.get(hover.path) : undefined;
  if (!hover || hover.kind !== "area" || !area) return null;
  return (
    <AreaHoverCard
      area={area}
      name={displayLabel(index, hover.path, rootLabel)}
      slot={hover.slot}
      figure={areaText.get(hover.path) ?? null}
      lensLabel={lens.label}
      analysed={analysed.get(hover.path) ?? null}
      labels={labels}
      lensCoversFiles={Boolean(lens.analysed) && (lens.areaFill?.(area) ?? null) === null}
      x={hover.x}
      y={hover.y}
      width={width}
      height={height}
    />
  );
}
