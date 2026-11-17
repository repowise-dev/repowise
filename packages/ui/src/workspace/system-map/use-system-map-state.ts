"use client";

/**
 * The map's two pieces of reader state, each controlled when the host passes
 * a change handler (so it can live in the URL) and held here otherwise: the
 * level state (open groups, focus, between, families) and the selection.
 * Setters are stable for the component's lifetime and read the live props
 * through a ref, so handlers built on them never go stale.
 */

import { useCallback, useRef, useState } from "react";
import type { SystemMapSelection } from "./types";
import { ESTATE_VIEW, sameMapView, type MapViewState } from "./view-state";

function sameSelection(a: SystemMapSelection, b: SystemMapSelection): boolean {
  return a?.type === b?.type && a?.id === b?.id;
}

export interface SystemMapStateArgs {
  view?: MapViewState | undefined;
  onViewChange?: ((view: MapViewState) => void) | undefined;
  selection?: SystemMapSelection | undefined;
  onSelectionChange?: ((selection: SystemMapSelection) => void) | undefined;
}

export interface SystemMapState {
  view: MapViewState;
  setView: (next: MapViewState) => void;
  selection: SystemMapSelection;
  setSelection: (next: SystemMapSelection) => void;
}

export function useSystemMapState({ view, onViewChange, selection, onSelectionChange }: SystemMapStateArgs): SystemMapState {
  const [ownView, setOwnView] = useState<MapViewState>(ESTATE_VIEW);
  const [ownSelection, setOwnSelection] = useState<SystemMapSelection>(null);
  const currentView = onViewChange ? (view ?? ESTATE_VIEW) : ownView;
  const currentSelection = onSelectionChange ? (selection ?? null) : ownSelection;

  const live = useRef({ currentView, currentSelection, onViewChange, onSelectionChange });
  live.current = { currentView, currentSelection, onViewChange, onSelectionChange };

  const setView = useCallback((next: MapViewState) => {
    const l = live.current;
    if (sameMapView(l.currentView, next)) return;
    l.currentView = next;
    if (l.onViewChange) l.onViewChange(next);
    else setOwnView(next);
  }, []);

  const setSelection = useCallback((next: SystemMapSelection) => {
    const l = live.current;
    if (sameSelection(l.currentSelection, next)) return;
    l.currentSelection = next;
    if (l.onSelectionChange) l.onSelectionChange(next);
    else setOwnSelection(next);
  }, []);

  return { view: currentView, setView, selection: currentSelection, setSelection };
}
