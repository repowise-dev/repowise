"use client";

import type {
  PerformanceContextFilter,
  PerformanceExecutionContext,
  PerformanceFacetCount,
  PerformanceFacetKey,
  PerformanceFacets,
} from "@repowise-dev/types/health";

import { FilterSelect } from "../code-health-controls";
import { ViewTabs } from "../../shared/view-tabs";
import {
  CONTEXT_HINT,
  CONTEXT_ORDER,
  FACET_LABEL,
  contextLabel,
  facetValueLabel,
} from "./presentation";
import {
  narrowingCount,
  type NarrowingKey,
  type PerformanceFilterState,
} from "./query";

/**
 * The filter surface. Every option and every count comes from the server's
 * facets, which are counted over the base result, so choosing a value never
 * removes its own alternatives from the list.
 */

function countOf(counts: PerformanceFacetCount[] | undefined, value: string): number {
  return counts?.find((entry) => entry.value === value)?.total ?? 0;
}

/**
 * The four canonical contexts stay separate and always visible, because a
 * context with no rows is a fact about the repository worth reading. When the
 * server predates the vocabulary they collapse to the pairing it understands.
 */
export function ContextTabs({
  value,
  facets,
  total,
  collapsed,
  onChange,
}: {
  value: PerformanceContextFilter;
  facets: PerformanceFacets;
  total: number;
  collapsed: boolean;
  onChange: (context: PerformanceContextFilter) => void;
}) {
  const counts = facets.context;
  const contexts: PerformanceExecutionContext[] = collapsed
    ? ["production", "test"]
    : CONTEXT_ORDER;
  const tabs = [
    { id: "all", label: "All", badge: total },
    ...contexts.map((context) => ({
      id: context,
      label:
        collapsed && context === "production" ? "Production & tooling" : contextLabel(context),
      badge:
        collapsed && context === "production"
          ? countOf(counts, "production") + countOf(counts, "tooling")
          : countOf(counts, context),
    })),
  ];
  return (
    <ViewTabs
      tabs={tabs}
      value={value}
      onValueChange={(next) => onChange(next as PerformanceContextFilter)}
    />
  );
}

interface NarrowingSpec {
  key: NarrowingKey;
  facet: PerformanceFacetKey;
  anyLabel: string;
}

const NARROWING: NarrowingSpec[] = [
  { key: "actionability", facet: "actionability", anyLabel: "Any" },
  { key: "boundary", facet: "boundary", anyLabel: "Any" },
  { key: "confidence", facet: "confidence", anyLabel: "Any" },
];

/**
 * A facet with one value is a fact, not a choice, so it is stated rather than
 * offered as a control that could only ever return the same rows.
 */
function SingleValueFact({ facet, only }: { facet: PerformanceFacetKey; only: PerformanceFacetCount }) {
  return (
    <p className="text-xs text-[var(--color-text-tertiary)]">
      <span className="uppercase tracking-wider">{FACET_LABEL[facet]}</span>{" "}
      <span className="text-[var(--color-text-secondary)]">
        {facetValueLabel(facet, only.value)}
      </span>{" "}
      <span className="tabular-nums">on all {only.total.toLocaleString()}</span>
    </p>
  );
}

export function QueueFilters({
  filters,
  facets,
  onChange,
  onClear,
}: {
  filters: PerformanceFilterState;
  facets: PerformanceFacets;
  onChange: (key: NarrowingKey, value: string | null) => void;
  onClear: () => void;
}) {
  const active = narrowingCount(filters);
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
      {NARROWING.map(({ key, facet, anyLabel }) => {
        const counts = facets[facet] ?? [];
        if (counts.length === 0) return null;
        if (counts.length === 1 && !filters[key]) {
          return <SingleValueFact key={key} facet={facet} only={counts[0]!} />;
        }
        return (
          <FilterSelect
            key={key}
            label={FACET_LABEL[facet]}
            value={filters[key] ?? ""}
            onChange={(next) => onChange(key, next || null)}
            options={[
              { value: "", label: anyLabel },
              ...counts.map((entry) => ({
                value: entry.value,
                label: `${facetValueLabel(facet, entry.value)} (${entry.total.toLocaleString()})`,
              })),
            ]}
          />
        );
      })}
      {active > 0 ? (
        <button
          type="button"
          onClick={onClear}
          className="rounded text-xs font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          Clear {active} filter{active === 1 ? "" : "s"}
        </button>
      ) : null}
    </div>
  );
}

/**
 * What the queue currently describes, and what it was drawn from. The
 * per-page range is the pagination control's job, so this states only what
 * that control cannot: how the selection narrows the repository, and when the
 * answer was computed.
 */
export function ScopeLine({
  filteredTotal,
  repositoryTotal,
  context,
  narrowed,
  analyzedCommit,
}: {
  filteredTotal: number;
  repositoryTotal: number;
  context: PerformanceContextFilter;
  narrowed: number;
  analyzedCommit?: string | null | undefined;
}) {
  const scope =
    context === "all"
      ? "opportunities"
      : `${contextLabel(context as PerformanceExecutionContext).toLowerCase()} opportunities`;
  return (
    <p role="status" className="text-xs text-[var(--color-text-tertiary)]">
      <span className="tabular-nums">{filteredTotal.toLocaleString()}</span> {scope}
      {narrowed > 0 ? ` under ${narrowed} filter${narrowed === 1 ? "" : "s"}` : ""}
      {filteredTotal !== repositoryTotal ? (
        <>
          , from <span className="tabular-nums">{repositoryTotal.toLocaleString()}</span> in the
          repository
        </>
      ) : null}
      .
      {analyzedCommit ? (
        <>
          {" "}
          Analyzed at <span className="font-mono">{analyzedCommit.slice(0, 7)}</span>.
        </>
      ) : null}
    </p>
  );
}

export function ContextHint({ context }: { context: PerformanceContextFilter }) {
  if (context === "all") return null;
  return (
    <p className="text-xs text-[var(--color-text-tertiary)]">
      {CONTEXT_HINT[context as PerformanceExecutionContext]}
    </p>
  );
}

