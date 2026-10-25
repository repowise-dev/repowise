"use client";

/**
 * From graph and level state to what the canvas draws: the drawn levels
 * view, its positions (off the main thread, cached per scope), and the
 * selection mapped onto it. When a selection from the URL or a list below
 * the map is hidden at the current level, opens what holds it, once per
 * selection.
 */

import { useEffect, useMemo, useRef } from "react";
import type { SystemEdge, SystemGraph, SystemNode } from "@repowise-dev/types";
import { kindCountsByFamily } from "./edge-families";
import { buildLevelView, type LevelView } from "./levels";
import { resolveSelection, type ResolvedSelection } from "./selection-fix";
import type { SystemMapSelection } from "./types";
import { useLevelLayout, type LevelLayout } from "./use-level-layout";
import { useSystemMapPhases, type SystemMapPhase } from "./use-system-map-phases";
import { sameMapView, toLevelViewState, type MapViewState } from "./view-state";

export interface MapLevel {
  nodesById: ReadonlyMap<string, SystemNode>;
  edgesById: ReadonlyMap<string, SystemEdge>;
  kindCounts: ReturnType<typeof kindCountsByFamily>;
  levelView: LevelView | null;
  layout: LevelLayout;
  resolved: ResolvedSelection | null;
}

export function useMapLevel(
  graph: SystemGraph | null,
  view: MapViewState,
  selection: SystemMapSelection,
  setView: (next: MapViewState) => void,
  onPhase?: (phase: SystemMapPhase) => void,
): MapLevel {
  const nodesById = useMemo(() => new Map<string, SystemNode>((graph?.nodes ?? []).map((n) => [n.id, n])), [graph]);
  const edgesById = useMemo(() => new Map<string, SystemEdge>((graph?.edges ?? []).map((e) => [e.id, e])), [graph]);
  const kindCounts = useMemo(() => kindCountsByFamily(graph?.edges ?? []), [graph]);

  const levelState = useMemo(() => toLevelViewState(view), [view]);
  const levelView = useMemo(() => (graph ? buildLevelView(graph, levelState) : null), [graph, levelState]);
  const layout = useLevelLayout({ graph, view: levelView });
  useSystemMapPhases(onPhase, levelView, layout.loading);

  const resolved = useMemo(
    () => (levelView ? resolveSelection(selection, levelView, edgesById, view) : null),
    [selection, levelView, edgesById, view],
  );
  const fix = resolved?.fix ?? null;
  const fixedFor = useRef<string | null>(null);
  useEffect(() => {
    const key = selection ? `${selection.type}:${selection.id}` : null;
    // Nothing to fix: forget the last fix, so reselecting a hidden service opens it again.
    if (!fix) {
      fixedFor.current = null;
      return;
    }
    if (fixedFor.current === key) return;
    fixedFor.current = key;
    if (!sameMapView(fix, view)) setView(fix);
  }, [selection, fix, view, setView]);

  return { nodesById, edgesById, kindCounts, levelView, layout, resolved };
}

/**
 * A group or band selection names something drawn at one level only. Once
 * the level changes and it is no longer drawn, it is dropped, so the rail
 * and Escape never act on a selection the reader cannot see.
 */
export function useDropUndrawnAggregate(
  selection: SystemMapSelection,
  level: MapLevel,
  setSelection: (next: SystemMapSelection) => void,
): void {
  const stale =
    (selection?.type === "group" || selection?.type === "band") && level.levelView !== null && !level.resolved?.drawnId;
  useEffect(() => {
    if (stale) setSelection(null);
  }, [stale, setSelection]);
}
