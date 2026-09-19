"use client";

import * as React from "react";

import { formatTokens } from "../lib/format";
import { cn } from "../lib/cn";

export interface SourceRow {
  /** The row's name, already resolved. A component here does not own a label
   *  map: the caller knows whether it is holding surfaces, operations, models
   *  or agents. */
  label: string;
  events: number;
  savedInputTokens: number;
}

export interface SavingsSourceTableProps {
  rows: SourceRow[];
  /**
   * Denominator for the proportion bars. The caller passes the report's total
   * rather than the largest row, so bars stay comparable between two tables on
   * one page -- normalising each table to its own max makes a small source
   * look as large as the biggest one.
   */
  total: number;
  /** Column header for the name column. */
  nameHeader: string;
  /** Rendered when `rows` is empty. */
  empty?: React.ReactNode;
  /** Accessible caption. Required: the table is a data claim and a screen
   *  reader arriving at it needs to know what it counts. */
  caption: string;
  className?: string;
}

/**
 * Where the savings came from, as full-width rows.
 *
 * A table rather than a chart because the question is "how much did each
 * source contribute", which is exact comparison, and there are four surfaces.
 * The bar is a second read of the same number in the same row, not a separate
 * encoding: one family, quieter step, no category palette. The previous
 * version tinted rows from the language-token ramp, which gave operations the
 * colours that mean Python and Rust elsewhere in the product.
 */
export function SavingsSourceTable({
  rows,
  total,
  nameHeader,
  empty,
  caption,
  className,
}: SavingsSourceTableProps) {
  if (rows.length === 0) {
    return empty ? <>{empty}</> : null;
  }

  return (
    // Scrolls inside its own container so the document never scrolls sideways
    // at 390px.
    <div className={cn("w-full overflow-x-auto", className)}>
      {/* Narrow enough that all four columns land inside a 390px viewport's
          content box. At 22rem the token column sat half off the edge and read
          as broken even though the container scrolled correctly. */}
      <table className="w-full min-w-[19rem] border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-[var(--color-border-default)] text-left">
            <Th className="w-[30%]">{nameHeader}</Th>
            <Th className="w-[34%]">Share</Th>
            <Th align="right">Events</Th>
            {/* Wraps to two lines when narrow rather than dropping the unit:
                "Saved" alone does not say saved what. */}
            <Th align="right">Tokens saved</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.label}
              className="border-b border-[var(--color-border-default)] last:border-b-0"
            >
              <td className="py-2.5 pr-3 text-[var(--color-text-primary)]">{row.label}</td>
              <td className="py-2.5 pr-3">
                <Share value={row.savedInputTokens} total={total} label={row.label} />
              </td>
              <td className="py-2.5 pr-3 text-right tabular-nums text-[var(--color-text-secondary)]">
                {row.events.toLocaleString()}
              </td>
              <td className="py-2.5 text-right tabular-nums text-[var(--color-text-primary)]">
                {formatTokens(row.savedInputTokens)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({
  children,
  align = "left",
  className,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={cn(
        "py-2 font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]",
        align === "right" ? "pl-3 text-right" : "pr-3 text-left",
        className,
      )}
    >
      {children}
    </th>
  );
}

/**
 * The row's share of the total, as a bar with its percentage in text.
 *
 * The number is written out rather than left to the bar alone: a 2% bar and a
 * 4% bar are indistinguishable at this width, and the percentage is the thing
 * a reader is comparing.
 */
function Share({ value, total, label }: { value: number; total: number; label: string }) {
  if (total <= 0) return <span className="text-[var(--color-text-tertiary)]">&mdash;</span>;
  const pct = (value / total) * 100;
  // A contributing row never renders as an empty track: below about half a
  // percent the bar rounds to nothing and the row reads as zero.
  const width = value > 0 ? Math.max(pct, 1.5) : 0;

  return (
    <span className="flex items-center gap-2">
      <span
        aria-hidden
        className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--color-bg-inset)]"
      >
        <span
          className="block h-full rounded-full bg-[var(--color-accent-fill)]"
          style={{ width: `${width}%` }}
        />
      </span>
      <span className="w-10 shrink-0 text-right text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {pct >= 1 ? `${Math.round(pct)}%` : "<1%"}
      </span>
      <span className="sr-only">
        {label}: {pct.toFixed(1)}% of total savings
      </span>
    </span>
  );
}
