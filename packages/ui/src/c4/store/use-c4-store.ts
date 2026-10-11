"use client";

/**
 * Minimal hook-based store for the C4 view.
 *
 * State is intentionally tiny — level + active container + selected node id —
 * so we don't pull in zustand for what amounts to three setters. The host
 * (web page) usually mirrors these into URL params via nuqs; this hook is the
 * fallback when used standalone (Storybook, tests, hosted frontend).
 */

import { useCallback, useState } from "react";
import type { C4Level } from "../types";

export interface C4StoreState {
  level: C4Level;
  activeContainerId: string | null;
  selectedNodeId: string | null;
}

export interface C4Store extends C4StoreState {
  setLevel: (level: C4Level) => void;
  drillIntoContainer: (containerId: string) => void;
  drillOut: () => void;
  selectNode: (nodeId: string | null) => void;
  reset: () => void;
}

export interface UseC4StoreOptions {
  initialLevel?: C4Level;
  initialContainerId?: string | null;
  onChange?: (state: C4StoreState) => void;
}

const DEFAULT_STATE: C4StoreState = {
  level: 2,
  activeContainerId: null,
  selectedNodeId: null,
};

export function useC4Store(options: UseC4StoreOptions = {}): C4Store {
  const { initialLevel = 2, initialContainerId = null, onChange } = options;

  const [state, setState] = useState<C4StoreState>({
    level: initialLevel,
    activeContainerId: initialContainerId,
    selectedNodeId: null,
  });

  const update = useCallback(
    (next: C4StoreState) => {
      setState(next);
      onChange?.(next);
    },
    [onChange],
  );

  const setLevel = useCallback(
    (level: C4Level) =>
      update({
        level,
        activeContainerId: level === 3 ? state.activeContainerId : null,
        selectedNodeId: null,
      }),
    [state.activeContainerId, update],
  );

  const drillIntoContainer = useCallback(
    (containerId: string) =>
      update({ level: 3, activeContainerId: containerId, selectedNodeId: null }),
    [update],
  );

  const drillOut = useCallback(() => {
    if (state.level === 3) {
      update({ level: 2, activeContainerId: null, selectedNodeId: null });
    } else if (state.level === 2) {
      update({ level: 1, activeContainerId: null, selectedNodeId: null });
    }
  }, [state.level, update]);

  const selectNode = useCallback(
    (nodeId: string | null) => update({ ...state, selectedNodeId: nodeId }),
    [state, update],
  );

  const reset = useCallback(() => update(DEFAULT_STATE), [update]);

  return { ...state, setLevel, drillIntoContainer, drillOut, selectNode, reset };
}
