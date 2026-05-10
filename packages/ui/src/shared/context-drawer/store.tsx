"use client";

import * as React from "react";
import type { EntityRef } from "../entity/types";

/**
 * Minimal context-drawer store. Kept dependency-free (no zustand) so the
 * component is portable to the hosted frontend without bringing in a state
 * library that wouldn't otherwise be needed.
 *
 * Consumers wire URL synchronization at their app layer (see web layout).
 */
export interface ContextDrawerState {
  entity: EntityRef | null;
  open: (entity: EntityRef) => void;
  close: () => void;
}

const ContextDrawerContext = React.createContext<ContextDrawerState | null>(null);

export interface ContextDrawerProviderProps {
  children: React.ReactNode;
  /** Optional initial entity (e.g. from URL hydration). */
  initialEntity?: EntityRef | null;
  /** Notified whenever the open entity changes — wire to URL sync here. */
  onEntityChange?: (entity: EntityRef | null) => void;
}

export function ContextDrawerProvider({
  children,
  initialEntity = null,
  onEntityChange,
}: ContextDrawerProviderProps) {
  const [entity, setEntity] = React.useState<EntityRef | null>(initialEntity);

  // Sync external prop changes (e.g. URL hydration on navigation).
  React.useEffect(() => {
    setEntity(initialEntity);
  }, [initialEntity?.kind, initialEntity?.id, initialEntity?.repoId]);

  const open = React.useCallback(
    (next: EntityRef) => {
      setEntity(next);
      onEntityChange?.(next);
    },
    [onEntityChange],
  );

  const close = React.useCallback(() => {
    setEntity(null);
    onEntityChange?.(null);
  }, [onEntityChange]);

  const value = React.useMemo<ContextDrawerState>(
    () => ({ entity, open, close }),
    [entity, open, close],
  );

  return (
    <ContextDrawerContext.Provider value={value}>{children}</ContextDrawerContext.Provider>
  );
}

export function useContextDrawer(): ContextDrawerState {
  const ctx = React.useContext(ContextDrawerContext);
  if (!ctx) {
    // Soft fallback: returning a no-op store keeps EntityLink callers from
    // crashing when no provider is mounted (e.g. during isolated component
    // tests). The real provider is mounted in the web app layout.
    return {
      entity: null,
      open: () => undefined,
      close: () => undefined,
    };
  }
  return ctx;
}
