"use client";

import * as React from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { cn } from "../../lib/cn";

/**
 * ResponsiveTable — shared table primitive with a column-priority API.
 *
 * Every column declares a `priority`:
 *   1 — always visible (identity / primary metric columns)
 *   2 — hidden below `md` (768px)
 *   3 — hidden below `lg` (1024px)
 *
 * The wrapper always provides `overflow-x-auto` as a fallback so nothing is
 * ever clipped. With `stacked`, the table additionally collapses to a
 * stacked card list below the given breakpoint: the first priority-1 column
 * renders as the card title and the remaining columns render as label/value
 * rows (via `mobileRender` when provided, else `render`).
 *
 * Purely presentational: rows in, callbacks out. No routing, no data fetching.
 */

export type ColumnPriority = 1 | 2 | 3;

export interface ResponsiveColumn<T> {
  /** Stable identifier; doubles as the sort key passed to `onSort`. */
  key: string;
  header: React.ReactNode;
  /** Short label used in stacked-card mode; falls back to `header` when it is a string. */
  mobileLabel?: string | undefined;
  align?: "left" | "right" | "center" | undefined;
  priority?: ColumnPriority | undefined;
  sortable?: boolean | undefined;
  headerClassName?: string | undefined;
  cellClassName?: string | undefined;
  render: (row: T) => React.ReactNode;
  /** Override rendering inside stacked cards (e.g. drop icons, shorten text). */
  mobileRender?: ((row: T) => React.ReactNode) | undefined;
  /** Skip this column entirely in stacked-card mode. */
  hideInCard?: boolean | undefined;
}

export interface ResponsiveTableProps<T> {
  columns: ResponsiveColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: ((row: T) => void) | undefined;
  selectedKey?: string | null | undefined;
  sortField?: string | undefined;
  sortOrder?: "asc" | "desc" | undefined;
  onSort?: ((key: string) => void) | undefined;
  /** Rendered instead of the table when `rows` is empty (use EmptyState). */
  empty?: React.ReactNode | undefined;
  /** Collapse to stacked cards below this breakpoint. Default: no collapse. */
  stacked?: "sm" | "md" | undefined;
  /** Extra classes on the outer wrapper. */
  className?: string | undefined;
  /** Omit the default rounded border + surface background. */
  bare?: boolean | undefined;
}

const PRIORITY_CELL_CLS: Record<ColumnPriority, string> = {
  1: "",
  2: "max-md:hidden",
  3: "max-lg:hidden",
};

const STACKED_TABLE_CLS: Record<NonNullable<ResponsiveTableProps<unknown>["stacked"]>, string> = {
  sm: "max-sm:hidden",
  md: "max-md:hidden",
};

const STACKED_CARDS_CLS: Record<NonNullable<ResponsiveTableProps<unknown>["stacked"]>, string> = {
  sm: "sm:hidden",
  md: "md:hidden",
};

function alignCls(align?: "left" | "right" | "center"): string {
  return align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left";
}

export function ResponsiveTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
  sortField,
  sortOrder = "desc",
  onSort,
  empty,
  stacked,
  className,
  bare,
}: ResponsiveTableProps<T>) {
  if (rows.length === 0 && empty) {
    return <>{empty}</>;
  }

  const table = (
    <div className={cn("overflow-x-auto", stacked && STACKED_TABLE_CLS[stacked])}>
      <table className="w-full text-sm">
        <thead className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider sticky top-0 z-10">
          <tr>
            {columns.map((c) => {
              const isActive = c.sortable && c.key === sortField;
              return (
                <th
                  key={c.key}
                  className={cn(
                    "px-3 py-2 font-medium whitespace-nowrap",
                    alignCls(c.align),
                    PRIORITY_CELL_CLS[c.priority ?? 1],
                    c.sortable && onSort && "cursor-pointer select-none",
                    c.headerClassName,
                  )}
                  onClick={c.sortable && onSort ? () => onSort(c.key) : undefined}
                  aria-sort={
                    isActive ? (sortOrder === "asc" ? "ascending" : "descending") : undefined
                  }
                >
                  <span className="inline-flex items-center gap-1">
                    {c.header}
                    {isActive ? (
                      sortOrder === "asc" ? (
                        <ArrowUp className="h-3 w-3" />
                      ) : (
                        <ArrowDown className="h-3 w-3" />
                      )
                    ) : null}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const key = rowKey(row);
            const isSelected = selectedKey != null && selectedKey === key;
            return (
              <tr
                key={key}
                className={cn(
                  "border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]",
                  isSelected && "bg-[var(--color-accent-muted)]/30",
                  onRowClick && "cursor-pointer",
                )}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cn(
                      "px-3 py-2",
                      alignCls(c.align),
                      PRIORITY_CELL_CLS[c.priority ?? 1],
                      c.cellClassName,
                    )}
                  >
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  const cards = stacked ? (
    <ul className={cn("divide-y divide-[var(--color-border-default)]", STACKED_CARDS_CLS[stacked])}>
      {rows.map((row) => {
        const key = rowKey(row);
        const isSelected = selectedKey != null && selectedKey === key;
        const [titleCol, ...rest] = columns.filter((c) => !c.hideInCard);
        if (!titleCol) return null;
        return (
          <li
            key={key}
            className={cn(
              "px-3 py-2.5",
              isSelected && "bg-[var(--color-accent-muted)]/30",
              onRowClick && "cursor-pointer hover:bg-[var(--color-bg-elevated)]",
            )}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
          >
            <div className="text-sm text-[var(--color-text-primary)]">
              {(titleCol.mobileRender ?? titleCol.render)(row)}
            </div>
            <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
              {rest.map((c) => {
                const value = (c.mobileRender ?? c.render)(row);
                if (value == null || value === "") return null;
                return (
                  <div key={c.key} className="flex items-baseline gap-1.5 text-xs">
                    <dt className="text-[var(--color-text-tertiary)]">
                      {c.mobileLabel ?? (typeof c.header === "string" ? c.header : c.key)}
                    </dt>
                    <dd className="text-[var(--color-text-secondary)] tabular-nums">{value}</dd>
                  </div>
                );
              })}
            </dl>
          </li>
        );
      })}
    </ul>
  ) : null;

  return (
    <div
      className={cn(
        !bare &&
          "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]",
        className,
      )}
    >
      {table}
      {cards}
    </div>
  );
}
