"use client";

/**
 * The Refactoring surface.
 *
 * Lede, then the structural plans as a field with the top few ranked under it,
 * then every plan as hairline rows. What used to be here was a priority-by-
 * effort quadrant over a grid of cards; both were replaced for reasons recorded
 * in `structural-map.tsx` and `plan-rows.tsx`.
 *
 * The list controls survive largely intact — search, sort, effort — with one
 * removal: the confidence filter no longer offers "low", because no detector
 * has ever emitted a low-confidence plan and the chip filtered to zero every
 * time it was clicked. It is built from the confidences actually present rather
 * than from the type, so it disappears entirely on a repo whose plans are all
 * one confidence.
 */

import * as React from "react";
import { Search } from "lucide-react";

import { Input } from "../ui/input";
import { PlanRows } from "./plan-rows";
import { RefactoringLede } from "./refactoring-lede";
import { StartHere } from "./start-here";
import { CONFIDENCE_LABEL } from "./meta";
import {
  blastCount,
  isStructural,
  type Confidence,
  type EffortBucket,
  type RefactoringPlan,
} from "./types";

const PAGE_SIZE = 60;

const EFFORTS: EffortBucket[] = ["S", "M", "L", "XL"];
const EFFORT_RANK: Record<EffortBucket, number> = { S: 0, M: 1, L: 2, XL: 3 };
const EFFORT_LABEL_LONG: Record<EffortBucket, string> = {
  S: "Small",
  M: "Medium",
  L: "Large",
  XL: "Extra large",
};
const CONFIDENCE_ORDER: Confidence[] = ["high", "medium", "low"];

type SortKey = "leverage" | "health" | "effort" | "blast" | "file";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  // "Leverage" names what the backend rank actually blends: recovered health,
  // how depended-upon the file is, and how much rides along. It used to be
  // labelled "Priority", which named nothing.
  { value: "leverage", label: "Leverage" },
  { value: "health", label: "Health recovered" },
  { value: "effort", label: "Effort, small first" },
  { value: "blast", label: "Files touched" },
  { value: "file", label: "File, A to Z" },
];

export interface RefactoringBoardProps {
  /** Plans for the active type filter. */
  plans: RefactoringPlan[];
  /** Every plan, unfiltered — the lede and Start here describe the whole repo,
   *  not the tab you happen to be on. This is also where the per-type counts
   *  come from, which is why the board no longer takes a `summary`: the
   *  endpoint's summary and the plan list could disagree under a filter, and
   *  two sources for one number is how a tab badge starts lying. */
  allPlans?: RefactoringPlan[] | undefined;
  indexedFileCount?: number | undefined;
  onOpen?: ((plan: RefactoringPlan) => void) | undefined;
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  /** Jump the type filter to the structural set. */
  onSeeStructural?: (() => void) | undefined;
  /** Hide the lede and Start here — for hosts that render their own header. */
  showLede?: boolean;
  emptyTitle?: string;
  emptyHint?: string;
}

export function RefactoringBoard({
  plans,
  allPlans,
  indexedFileCount,
  onOpen,
  onAiPrompt,
  fileHref,
  onSeeStructural,
  showLede = true,
  emptyTitle = "No refactoring plans",
  emptyHint = "Plans appear here when a file is worth splitting, a cycle worth cutting, a class worth extracting, or a repeated block worth sharing.",
}: RefactoringBoardProps) {
  const [query, setQuery] = React.useState("");
  const [sortKey, setSortKey] = React.useState<SortKey>("leverage");
  const [effortSel, setEffortSel] = React.useState<Set<EffortBucket>>(new Set());
  const [confSel, setConfSel] = React.useState<Set<Confidence>>(new Set());
  const [visibleCount, setVisibleCount] = React.useState(PAGE_SIZE);

  const every = allPlans ?? plans;
  const structural = React.useMemo(() => every.filter(isStructural), [every]);

  // Only offer confidences that occur. A filter is worth building where there
  // is something to subtract from.
  const confidencesPresent = React.useMemo(() => {
    const present = new Set(every.map((p) => p.confidence || "medium"));
    return CONFIDENCE_ORDER.filter((c) => present.has(c));
  }, [every]);

  const toggle = React.useCallback(
    <T,>(setter: React.Dispatch<React.SetStateAction<Set<T>>>, value: T) => {
      setter((cur) => {
        const next = new Set(cur);
        if (next.has(value)) next.delete(value);
        else next.add(value);
        return next;
      });
    },
    [],
  );

  const processed = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    let out = plans.filter((p) => {
      if (q && !`${p.file_path} ${p.target_symbol}`.toLowerCase().includes(q)) return false;
      if (effortSel.size && !effortSel.has((p.effort_bucket || "M") as EffortBucket)) return false;
      if (confSel.size && !confSel.has((p.confidence || "medium") as Confidence)) return false;
      return true;
    });
    // `leverage` keeps the backend's rank order; the rest sort a copy.
    if (sortKey !== "leverage") {
      out = [...out].sort((a, b) => {
        switch (sortKey) {
          case "health":
            return b.impact_delta - a.impact_delta;
          case "effort":
            return (
              EFFORT_RANK[(a.effort_bucket || "M") as EffortBucket] -
              EFFORT_RANK[(b.effort_bucket || "M") as EffortBucket]
            );
          case "blast":
            return blastCount(b) - blastCount(a);
          case "file":
            return a.file_path.localeCompare(b.file_path);
          default:
            return 0;
        }
      });
    }
    return out;
  }, [plans, query, sortKey, effortSel, confSel]);

  React.useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [processed]);

  if (every.length === 0) {
    return (
      <div className="border-t border-[var(--color-border-default)] pt-10 text-center">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{emptyTitle}</h3>
        <p className="mx-auto mt-1.5 max-w-[56ch] text-sm text-[var(--color-text-tertiary)]">
          {emptyHint}
        </p>
      </div>
    );
  }

  const filtersActive = query.trim() !== "" || effortSel.size > 0 || confSel.size > 0;

  return (
    <div className="space-y-10">
      {showLede ? (
        <RefactoringLede plans={every} indexedFileCount={indexedFileCount} />
      ) : null}

      {showLede && structural.length > 0 ? (
        <StartHere plans={structural} onOpen={onOpen} onSeeAll={onSeeStructural} />
      ) : null}

      <section className="space-y-4 border-t border-[var(--color-border-default)] pt-8">
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">All plans</h2>
          <p className="mt-1 max-w-[68ch] text-sm text-[var(--color-text-secondary)]">
            Ordered by leverage. Every plan opens the same inspector: what changes, why it was
            flagged, and what else it touches.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search file or symbol"
              className="pl-9"
              aria-label="Search plans"
            />
          </div>
          <div className="flex items-center gap-2">
            <label
              htmlFor="refactoring-sort"
              className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
            >
              Sort
            </label>
            <select
              id="refactoring-sort"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              className="h-9 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 text-sm text-[var(--color-text-primary)] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-accent-primary)]"
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Effort
            </span>
            {EFFORTS.map((e) => (
              <FilterChip
                key={e}
                active={effortSel.has(e)}
                onClick={() => toggle(setEffortSel, e)}
                label={EFFORT_LABEL_LONG[e]}
              />
            ))}
          </div>
          {confidencesPresent.length > 1 ? (
            <div className="flex items-center gap-2">
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                Confidence
              </span>
              {confidencesPresent.map((c) => (
                <FilterChip
                  key={c}
                  active={confSel.has(c)}
                  onClick={() => toggle(setConfSel, c)}
                  label={CONFIDENCE_LABEL[c]}
                />
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold tabular-nums text-[var(--color-text-primary)]">
            {processed.length.toLocaleString()} plan{processed.length === 1 ? "" : "s"}
            {filtersActive ? (
              <span className="font-normal text-[var(--color-text-tertiary)]">
                {" "}
                of {plans.length.toLocaleString()}
              </span>
            ) : null}
          </h3>
          {filtersActive ? (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                setEffortSel(new Set());
                setConfSel(new Set());
              }}
              className="text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-text-primary)] hover:underline"
            >
              Clear filters
            </button>
          ) : null}
        </div>

        {processed.length === 0 ? (
          <p className="border-t border-[var(--color-border-default)] py-10 text-center text-sm text-[var(--color-text-tertiary)]">
            No plans match these filters.
          </p>
        ) : (
          <>
            <PlanRows
              plans={processed.slice(0, visibleCount)}
              onOpen={onOpen}
              onAiPrompt={onAiPrompt}
              fileHref={fileHref}
            />
            {processed.length > visibleCount ? (
              <div className="flex justify-center border-t border-[var(--color-border-default)] pt-5">
                <button
                  type="button"
                  onClick={() => setVisibleCount((n) => n + PAGE_SIZE)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-sm font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)]"
                >
                  Show {Math.min(PAGE_SIZE, processed.length - visibleCount)} more
                  <span className="tabular-nums text-[var(--color-text-tertiary)]">
                    · {(processed.length - visibleCount).toLocaleString()} left
                  </span>
                </button>
              </div>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors ${
        active
          ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
          : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)]"
      }`}
    >
      {label}
    </button>
  );
}
