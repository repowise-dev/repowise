"use client";

import * as React from "react";
import { cn } from "../../lib/cn";
import { useVirtualRows } from "./use-virtual-rows";

/**
 * VirtualizedTable — a real `<table>` whose `<tbody>` rows are windowed.
 *
 * It keeps the semantic table (so column alignment, sticky headers and the
 * caller's own `<tr>`/`<td>` markup all just work) and virtualizes via the
 * "padding row" pattern: a spacer row above and below the rendered window stand
 * in for the off-screen rows. Below the hook's threshold every row renders, so
 * small tables behave exactly as before.
 *
 * The caller owns the header cells (`header`) and each row's cells
 * (`renderRow`). For variable-height rows (e.g. expandable detail rows) spread
 * the supplied `measureRef` and `data-index` onto the `<tr>` so the windowing
 * tracks real heights:
 *
 *   renderRow={(row, index, measureRef) => (
 *     <tr ref={measureRef} data-index={index}>…</tr>
 *   )}
 */
export interface VirtualizedTableProps<T> {
  rows: T[];
  /** Stable key per row. */
  rowKey: (row: T, index: number) => string;
  /** The header row(s) — `<tr><th>…</th></tr>` — rendered inside a sticky `<thead>`. */
  header: React.ReactNode;
  /** Render one row as a `<tr>` (or a fragment of `<tr>`s for expandable rows). */
  renderRow: (
    row: T,
    index: number,
    measureRef: (el: HTMLElement | null) => void,
  ) => React.ReactNode;
  /** Estimated/real row height in px. Default 44. */
  estimateRowHeight?: number;
  overscan?: number;
  threshold?: number;
  /** Max height of the scroll viewport (px when a number). Default 600. */
  maxHeight?: number | string;
  /** Scroll-container classes. */
  className?: string;
  tableClassName?: string;
  headerClassName?: string;
  /** Rendered below the table when there are no rows. */
  empty?: React.ReactNode;
  "aria-label"?: string;
}

export function VirtualizedTable<T>({
  rows,
  rowKey,
  header,
  renderRow,
  estimateRowHeight = 44,
  overscan,
  threshold,
  maxHeight = 600,
  className,
  tableClassName,
  headerClassName,
  empty,
  "aria-label": ariaLabel,
}: VirtualizedTableProps<T>) {
  const { scrollRef, virtualRows, paddingTop, paddingBottom, measureElement } =
    useVirtualRows({
      count: rows.length,
      estimateSize: estimateRowHeight,
      // Spread conditionally: under `exactOptionalPropertyTypes` a present-but-
      // undefined value is not assignable to an optional `number`.
      ...(overscan !== undefined ? { overscan } : {}),
      ...(threshold !== undefined ? { threshold } : {}),
    });

  return (
    <div ref={scrollRef} className={cn("overflow-auto", className)} style={{ maxHeight }}>
      <table className={cn("w-full border-collapse", tableClassName)} aria-label={ariaLabel}>
        <thead
          className={cn(
            "sticky top-0 z-10 bg-[var(--color-bg-elevated)]",
            headerClassName,
          )}
        >
          {header}
        </thead>
        <tbody>
          {paddingTop > 0 && (
            <tr aria-hidden>
              <td style={{ height: paddingTop, padding: 0, border: 0 }} />
            </tr>
          )}
          {virtualRows.map((vr) => {
            const row = rows[vr.index];
            if (row === undefined) return null;
            return (
              <React.Fragment key={rowKey(row, vr.index)}>
                {renderRow(row, vr.index, measureElement)}
              </React.Fragment>
            );
          })}
          {paddingBottom > 0 && (
            <tr aria-hidden>
              <td style={{ height: paddingBottom, padding: 0, border: 0 }} />
            </tr>
          )}
        </tbody>
      </table>
      {rows.length === 0 && empty}
    </div>
  );
}
