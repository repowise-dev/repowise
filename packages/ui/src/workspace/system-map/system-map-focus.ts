/**
 * Hover and selection for the map's edges, outside React state.
 *
 * Every edge asks one question: am I touching what the reader is pointing at?
 * Held in React state, a hover would re-render every edge on the canvas; here
 * each edge subscribes to its own boolean, so a hover re-renders only the
 * edges whose answer changed. The fade of everything else is one attribute on
 * the canvas and a CSS rule, not a render.
 */

import { createContext, useContext, useSyncExternalStore } from "react";

interface Focus {
  node: string | null;
  edge: string | null;
}

export interface SystemMapFocusStore {
  subscribe: (listener: () => void) => () => void;
  setHover: (focus: Partial<Focus>) => void;
  setSelected: (focus: Focus) => void;
  /** True when anything is hovered or selected. */
  focused: () => boolean;
  edgeActive: (id: string, source: string, target: string) => boolean;
}

export function createSystemMapFocusStore(): SystemMapFocusStore {
  let hover: Focus = { node: null, edge: null };
  let selected: Focus = { node: null, edge: null };
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((l) => l());

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    setHover(next) {
      const merged = { ...hover, ...next };
      if (merged.node === hover.node && merged.edge === hover.edge) return;
      hover = merged;
      emit();
    },
    setSelected(next) {
      if (next.node === selected.node && next.edge === selected.edge) return;
      selected = next;
      emit();
    },
    focused: () => Boolean(hover.node || hover.edge || selected.node || selected.edge),
    edgeActive(id, source, target) {
      if (id === hover.edge || id === selected.edge) return true;
      for (const n of [hover.node, selected.node]) {
        if (n && (n === source || n === target)) return true;
      }
      return false;
    },
  };
}

export const SystemMapFocusContext = createContext<SystemMapFocusStore | null>(null);

const never = () => () => {};

/** Whether this edge touches the hovered or selected object. */
export function useEdgeActive(id: string, source: string, target: string): boolean {
  const store = useContext(SystemMapFocusContext);
  return useSyncExternalStore(
    store ? store.subscribe : never,
    () => (store ? store.edgeActive(id, source, target) : false),
    () => false,
  );
}
