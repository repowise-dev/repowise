"use client";

/**
 * The top of the Refactoring page: the structural opportunities as a field, and
 * the handful worth doing first as ranked rows underneath.
 *
 * These are one section rather than two because they answer different halves of
 * the same question and neither is sufficient alone. The field says which work
 * is expensive and far-reaching, which is a shape a sorted column cannot carry
 * - a 1,417-line file with 11 dependents and a 1,052-line file with 195 are
 * neighbours in a list and opposite corners here. The rows say what to do
 * first, and carry the step count and the lead cause a mark has no room for.
 *
 * They share hover state in both directions, so the two halves read as one
 * object: pointing at a mark lights its row, and pointing at a row lights its
 * mark.
 *
 * Below `MAP_MIN_POINTS` plottable opportunities the field does not render. A
 * scatter with four marks asks a reader to decode two axes to learn less than
 * four rows already told them, and a repo with no large files would otherwise
 * get an empty centrepiece with a confident heading over it.
 */

import * as React from "react";

import { formatNumber } from "../lib/format";
import { StructuralMap } from "./structural-map";
import { typeMeta } from "./meta";
import { MAP_MIN_POINTS } from "./types";
import {
  addressesPrimaryShort,
  stepSummary,
  structuralMarks,
  type StructuralMark,
} from "./opportunity";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";

export interface StartHereProps {
  /** Structural opportunities, already in rank order. */
  opportunities: RefactoringOpportunity[];
  onOpen?: ((opportunity: RefactoringOpportunity) => void) | undefined;
  /** Jump to the full list filtered to structural types. */
  onSeeAll?: (() => void) | undefined;
  /** How many ranked rows to show under the field. */
  leadCount?: number;
  highlightedId?: string | null | undefined;
  onHighlight?: ((id: string | null) => void) | undefined;
}

const EFFORT_WORD: Record<string, string> = {
  S: "Small",
  M: "Medium",
  L: "Large",
  XL: "Extra large",
};

export function StartHere({
  opportunities,
  onOpen,
  onSeeAll,
  leadCount = 5,
  highlightedId,
  onHighlight,
}: StartHereProps) {
  const [localHighlight, setLocalHighlight] = React.useState<string | null>(null);
  const highlighted = highlightedId !== undefined ? highlightedId : localHighlight;
  const setHighlighted = onHighlight ?? setLocalHighlight;

  const marks = React.useMemo(() => structuralMarks(opportunities), [opportunities]);
  const showMap = marks.length >= MAP_MIN_POINTS;
  const lead = opportunities.slice(0, leadCount);
  const dropped = opportunities.length - marks.length;

  const byId = React.useMemo(() => {
    const map = new Map<string, RefactoringOpportunity>();
    for (const item of opportunities) map.set(item.opportunity_id, item);
    return map;
  }, [opportunities]);

  const onSelectMark = React.useCallback(
    (mark: StructuralMark) => {
      const item = byId.get(mark.opportunityId);
      if (item && onOpen) onOpen(item);
    },
    [byId, onOpen],
  );

  if (opportunities.length === 0) return null;

  return (
    <section className="space-y-5 border-t border-[var(--color-border-default)] pt-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">Start here</h2>
          <p className="mt-1 max-w-[68ch] text-sm text-[var(--color-text-secondary)]">
            {showMap
              ? "Up the page is a bigger file to work through; right is more code that imports it. Top right is where a morning changes the most, and where the back-compat shim matters. One mark is one file."
              : "The work that changes how the codebase is laid out, ranked by how depended-upon the file is and how much rides along with it."}
          </p>
        </div>
        {onSeeAll ? (
          <button
            type="button"
            onClick={onSeeAll}
            className="shrink-0 text-sm font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline"
          >
            See all {formatNumber(opportunities.length)} structural
          </button>
        ) : null}
      </div>

      {showMap ? (
        <StructuralMap
          marks={marks}
          dropped={dropped}
          onSelect={onOpen ? onSelectMark : undefined}
          highlightedId={highlighted}
          onHighlight={setHighlighted}
        />
      ) : null}

      <div>
        {lead.map((item, i) => {
          const meta = typeMeta(item.lead_refactoring_type || "");
          const effort = EFFORT_WORD[item.effort_bucket] ?? item.effort_bucket;
          const lit = item.opportunity_id === highlighted;
          return (
            <button
              key={item.opportunity_id}
              type="button"
              onClick={onOpen ? () => onOpen(item) : undefined}
              disabled={!onOpen}
              onMouseEnter={() => setHighlighted(item.opportunity_id)}
              onMouseLeave={() => setHighlighted(null)}
              onFocus={() => setHighlighted(item.opportunity_id)}
              onBlur={() => setHighlighted(null)}
              className={`group grid w-full grid-cols-[28px_minmax(0,1fr)] items-start gap-4 border-t border-[var(--color-border-default)] px-3 py-4 text-left transition-colors first:border-t-0 md:grid-cols-[28px_minmax(0,1fr)_170px] md:items-center ${
                lit ? "bg-[var(--color-accent-muted)]" : "hover:bg-[var(--color-bg-elevated)]"
              }`}
            >
              <span className="pt-0.5 font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0">
                <span className="block text-[15px] font-semibold text-[var(--color-text-primary)] group-hover:text-[var(--color-accent-primary)]">
                  {meta.label} · {item.file_path.split("/").pop()}
                </span>
                {/* Full path, no ellipsis: a truncated title reports a layout
                    decision to the reader as missing content. */}
                <span className="mt-0.5 block break-all font-mono text-[11px] text-[var(--color-text-tertiary)]">
                  {item.file_path}
                </span>
                <span className="mt-1.5 block max-w-[70ch] text-[13px] text-[var(--color-text-secondary)]">
                  {stepSummary(item)} ·{" "}
                  {addressesPrimaryShort(item.addresses_primary_problem).toLowerCase()}
                </span>
              </span>
              <span className="mt-2 block text-xs text-[var(--color-text-tertiary)] md:mt-0 md:text-right">
                {effort} effort
                <br />
                {item.confidence === "high"
                  ? "High"
                  : item.confidence === "low"
                    ? "Low"
                    : "Medium"}{" "}
                confidence
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
