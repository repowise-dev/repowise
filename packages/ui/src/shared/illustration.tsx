"use client";

import * as React from "react";

/**
 * Which shared state is asking for art.
 *
 * - `empty`: nothing indexed or no data yet (neutral `EmptyState`).
 * - `clean`: a check ran and found nothing to fix (positive `EmptyState`, `CleanState`).
 * - `notFound`: `RouteNotFound`.
 * - `error`: `RouteError` and error-tone `EmptyState`.
 */
export type IllustrationKind = "empty" | "clean" | "notFound" | "error";

export interface IllustrationRenderProps {
  /** The state's title, for picking or tuning art. The title is already on screen next to it. */
  title: string;
  /** Square size in px the state lays out for. */
  size: number;
}

export type IllustrationRenderer = (props: IllustrationRenderProps) => React.ReactNode;

export type Illustrations = Partial<Record<IllustrationKind, IllustrationRenderer>>;

const IllustrationContext = React.createContext<Illustrations>({});

/**
 * Lets a host draw its own art in the shared empty, clean, not-found and error
 * states. OSS mounts none, so those states render their built-in marks.
 * Renderers are functions, so mount this inside a client component.
 */
export function IllustrationProvider({
  illustrations,
  children,
}: {
  illustrations: Illustrations;
  children: React.ReactNode;
}) {
  return (
    <IllustrationContext.Provider value={illustrations}>{children}</IllustrationContext.Provider>
  );
}

/**
 * The provider's art for `kind`, or `fallback` when none is mounted. A client
 * leaf so the states that use it stay usable from server components.
 */
export function IllustrationSlot({
  kind,
  title,
  size,
  fallback,
}: IllustrationRenderProps & { kind: IllustrationKind; fallback: React.ReactNode }) {
  const render = React.useContext(IllustrationContext)[kind];
  if (!render) return <>{fallback}</>;
  return (
    <span aria-hidden className="inline-flex shrink-0">
      {render({ title, size })}
    </span>
  );
}
