"use client";

import { useState } from "react";
import type { PerformanceScope } from "@repowise-dev/types/health";

import { Segmented } from "../../shared/segmented";
import type { ExcludedReview } from "./grouping";
import { QUIET_LINK_CLS } from "./queue";

/**
 * The one scope control and the sentence that accounts for what the scope
 * leaves out. Every figure on the page is counted inside the selected scope;
 * the sentence is where the rest of the repository is named, each part a
 * link to the scope that lists it.
 */

export type ScopeCounts = Record<PerformanceScope, number>;

const SCOPE_LABEL: Record<PerformanceScope, string> = {
  now: "Worth doing now",
  production: "All production",
  all: "Everything",
};

const SCOPE_HINT: Record<PerformanceScope, string> = {
  now: "Production causes with a fix to make, whose cost is shown to grow with the data.",
  production: "Every production cause, including expected repetition and ones without a fix yet.",
  all: "Every cause, in tests, tooling and unclassified code too.",
};

export function ScopeControl({
  value,
  counts,
  onChange,
}: {
  value: PerformanceScope;
  counts: ScopeCounts;
  onChange: (scope: PerformanceScope) => void;
}) {
  return (
    <Segmented
      label="Scope"
      value={value}
      onChange={onChange}
      options={(["now", "production", "all"] as const).map((scope) => ({
        value: scope,
        label: SCOPE_LABEL[scope],
        count: counts[scope].toLocaleString(),
        hint: SCOPE_HINT[scope],
      }))}
    />
  );
}

/** One clause of the accounting sentence; `scope` makes its figure a link. */
export interface ScopeClause {
  count: number;
  text: string;
  scope: PerformanceScope | null;
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/**
 * What the selected scope shows and where the rest of the repository is, as
 * clauses. Zero-count clauses are dropped: nothing hidden is not worth a link.
 */
export function scopeAccount(scope: PerformanceScope, counts: ScopeCounts): ScopeClause[] {
  const { now, production, all } = counts;
  const outside = all - production;
  const waiting = production - now;
  const clauses: ScopeClause[] =
    scope === "now"
      ? [
          { count: now, text: "worth doing now", scope: null },
          {
            count: waiting,
            text: `more production ${plural(waiting, "cause is", "causes are")} not ready to act on`,
            scope: "production",
          },
          {
            count: outside,
            text: `${plural(outside, "is", "are")} outside production code`,
            scope: "all",
          },
        ]
      : scope === "production"
        ? [
            { count: production, text: plural(production, "production cause", "production causes"), scope: null },
            { count: now, text: "worth doing now", scope: "now" },
            {
              count: outside,
              text: `more ${plural(outside, "is", "are")} outside production code`,
              scope: "all",
            },
          ]
        : [
            { count: all, text: plural(all, "cause across all code", "causes across all code"), scope: null },
            { count: production, text: "in production", scope: "production" },
            { count: now, text: "worth doing now", scope: "now" },
          ];
  return clauses.filter((clause, index) => index === 0 || clause.count > 0);
}

/**
 * The accounting sentence, with the default queue's left-out breakdown behind
 * a disclosure on it rather than in a second list.
 */
export function ScopeSentence({
  scope,
  counts,
  reviews,
  onScope,
  onReview,
}: {
  scope: PerformanceScope;
  counts: ScopeCounts;
  reviews: ExcludedReview[];
  onScope: (scope: PerformanceScope) => void;
  onReview: (review: ExcludedReview) => void;
}) {
  const [open, setOpen] = useState(false);
  const clauses = scopeAccount(scope, counts);
  return (
    <div className="space-y-2 text-sm text-[var(--color-text-secondary)]">
      {/* Said once, visibly: a second hidden copy for screen readers was read
          (and copied) twice, and a live region around these links would
          re-announce them on every count change. */}
      <p>
        {clauses.map((clause, index) => (
          <span key={clause.text}>
            {index === 0 ? "" : scope === "now" ? ". " : ", "}
            {clause.scope ? (
              <button type="button" onClick={() => onScope(clause.scope!)} className={QUIET_LINK_CLS}>
                <span className="tabular-nums">{clause.count.toLocaleString()}</span> {clause.text}
              </button>
            ) : (
              <>
                <span className="tabular-nums">{clause.count.toLocaleString()}</span> {clause.text}
              </>
            )}
          </span>
        ))}
        .
        {reviews.length > 0 ? (
          <>
            {" "}
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
              className={`${QUIET_LINK_CLS} text-xs`}
            >
              {open ? "Hide why" : "Why left out?"}
            </button>
          </>
        ) : null}
      </p>
      {open ? <LeftOutBreakdown reviews={reviews} onReview={onReview} /> : null}
    </div>
  );
}

/** Why the default queue left each cause out, counts as text, reasons as links. */
function LeftOutBreakdown({
  reviews,
  onReview,
}: {
  reviews: ExcludedReview[];
  onReview: (review: ExcludedReview) => void;
}) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
      {reviews.map((review) => (
        <li key={review.reason}>
          <span className="tabular-nums">{review.count.toLocaleString()}</span>{" "}
          <button type="button" onClick={() => onReview(review)} className={QUIET_LINK_CLS}>
            {review.label}
          </button>
        </li>
      ))}
    </ul>
  );
}
