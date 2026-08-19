"use client";

import * as React from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

/**
 * useVirtualRows — the shared windowing primitive behind every big table in the
 * package. It wraps `@tanstack/react-virtual` and returns just the pieces a row
 * renderer needs: the scroll-container ref, the window of rows to render, and
 * the top/bottom spacer heights for the "padding row" virtualization pattern
 * (which keeps a real `<table>`/`<tbody>` intact — no absolute positioning or
 * column-width hacks).
 *
 * Below `threshold` rows, windowing is skipped entirely: every row is returned
 * with zero padding. That keeps small lists (and SSR / jsdom tests, where the
 * scroll viewport measures 0) trivially correct, and avoids paying for a
 * virtualizer when there is nothing to save.
 */
export interface UseVirtualRowsOptions {
  /** Total number of rows. */
  count: number;
  /**
   * Estimated row height in px. Uniform-height tables can pass their real row
   * height; for variable-height rows attach `measureElement` and this is only
   * the initial guess. Default 44.
   */
  estimateSize?: number;
  /** Extra rows rendered above/below the viewport. Default 8. */
  overscan?: number;
  /**
   * Below this many rows, render every row (no windowing). Default 60 — large
   * enough that any list small enough to render whole stays simple, small
   * enough that the 60fps-at-5k-rows goal is always windowed.
   */
  threshold?: number;
}

export interface VirtualRow {
  index: number;
  start: number;
  size: number;
}

export interface UseVirtualRows<E extends HTMLElement = HTMLDivElement> {
  /** Attach to the scroll container (needs `overflow: auto` + a bounded height). */
  scrollRef: React.RefObject<E | null>;
  /** The window of rows to render (every row when not virtualizing). */
  virtualRows: VirtualRow[];
  /** Spacer height before the first rendered row; 0 when not virtualizing. */
  paddingTop: number;
  /** Spacer height after the last rendered row; 0 when not virtualizing. */
  paddingBottom: number;
  /**
   * Ref callback for a rendered row element, enabling measured (variable)
   * heights. Attach as `ref={measureElement} data-index={index}` on the row.
   * A no-op when not virtualizing.
   */
  measureElement: (el: HTMLElement | null) => void;
  /** Whether windowing is active for the current count. */
  isVirtualized: boolean;
}

const DEFAULT_ESTIMATE = 44;
const DEFAULT_OVERSCAN = 8;
const DEFAULT_THRESHOLD = 60;

const NOOP_MEASURE = () => {};

export function useVirtualRows<E extends HTMLElement = HTMLDivElement>({
  count,
  estimateSize = DEFAULT_ESTIMATE,
  overscan = DEFAULT_OVERSCAN,
  threshold = DEFAULT_THRESHOLD,
}: UseVirtualRowsOptions): UseVirtualRows<E> {
  const scrollRef = React.useRef<E | null>(null);
  const enabled = count > threshold;

  const virtualizer = useVirtualizer({
    count,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimateSize,
    overscan,
    enabled,
  });

  // Read the windowed items unconditionally so the hook's behaviour only
  // depends on `enabled`, never on call order.
  const items = virtualizer.getVirtualItems();

  if (!enabled) {
    const virtualRows: VirtualRow[] = Array.from({ length: count }, (_, index) => ({
      index,
      start: index * estimateSize,
      size: estimateSize,
    }));
    return {
      scrollRef,
      virtualRows,
      paddingTop: 0,
      paddingBottom: 0,
      measureElement: NOOP_MEASURE,
      isVirtualized: false,
    };
  }

  const totalSize = virtualizer.getTotalSize();
  const first = items[0];
  const last = items[items.length - 1];
  const paddingTop = first ? first.start : 0;
  // With no window yet (first paint, before the container is measured) the whole
  // list is still "below", so the spacer has to carry the full height. Returning
  // 0 here deadlocks any scroll container whose only content is these rows: no
  // rows means no height, and a zero-height viewport makes the virtualizer
  // refuse to compute a range, so no rows ever appear.
  const paddingBottom = last ? totalSize - last.end : totalSize;

  return {
    scrollRef,
    virtualRows: items.map((i) => ({ index: i.index, start: i.start, size: i.size })),
    paddingTop,
    paddingBottom,
    measureElement: virtualizer.measureElement,
    isVirtualized: true,
  };
}
