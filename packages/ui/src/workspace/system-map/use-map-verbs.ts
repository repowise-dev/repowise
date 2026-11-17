"use client";

/**
 * What the reader can do to the map from a card, a connector or the rail:
 * select, open a group, focus a service, list what a "+N more" card folded,
 * and the rail's verbs (focus, routes to..., compare, show a family, leave
 * the comparison). Comparing goes through the navigation's one `onBetween`.
 */

import { useCallback, useMemo } from "react";
import { activateEdge, activateNode, type Activation, type ActivateTrigger } from "./activate";
import type { LevelView } from "./levels";
import type { SystemMapActions } from "./system-map-actions";
import type { RailVerbs } from "./system-map-rail-content";
import type { MapNavigation } from "./use-map-navigation";
import type { SystemMapState } from "./use-system-map-state";
import { closeContainer, focusService, openContainer, showFamily } from "./view-state";

/** Card verbs and the canvas's activate handler, over the level state and the selection. */
export function useMapInput(
  levelView: LevelView | null,
  state: SystemMapState,
  onShowFolded: ((serviceIds: readonly string[]) => void) | undefined,
) {
  const { view, setView, setSelection } = state;
  const actions = useMemo<SystemMapActions>(
    () => ({
      openContainer: (id) => setView(openContainer(view, id)),
      closeContainer: (id) => setView(closeContainer(view, id)),
      showFolded: onShowFolded,
    }),
    [setView, view, onShowFolded],
  );
  const apply = useCallback(
    (a: Activation) => {
      if (a?.kind === "select") setSelection(a.selection);
      else if (a?.kind === "open") actions.openContainer(a.id);
      else if (a?.kind === "focus") setView(focusService(view, a.serviceId));
      else if (a?.kind === "folded") actions.showFolded?.(a.serviceIds);
    },
    [setSelection, actions, setView, view],
  );
  const onActivate = useCallback(
    (id: string, kind: "node" | "edge", trigger: ActivateTrigger) =>
      apply(
        kind === "edge"
          ? activateEdge(levelView?.edges.find((e) => e.id === id))
          : activateNode(levelView?.nodes.find((n) => n.id === id), trigger),
      ),
    [apply, levelView],
  );
  return { actions, onActivate };
}

/** The verbs the rail offers. */
export function useRailVerbs(state: SystemMapState, nav: MapNavigation): RailVerbs {
  const { view, setView } = state;
  const { setPicker, onBetween, toMap } = nav;
  return useMemo<RailVerbs>(
    () => ({
      onFocus: (id) => setView(toMap(focusService(view, id))),
      onRoutesFrom: (id) => setPicker({ open: true, from: id }),
      onBetween,
      onShowFamily: (f) => setView(showFamily(view, f)),
      onLeaveBetween: () => setView({ ...view, between: null }),
    }),
    [view, setView, setPicker, onBetween, toMap],
  );
}
