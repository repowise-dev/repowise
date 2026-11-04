"use client";

import type {
  PerformanceFacetCount,
  PerformanceFacetKey,
  PerformanceFacets,
} from "@repowise-dev/types/health";

import { FilterSelect } from "../code-health-controls";
import { MoreFilters } from "../../shared/more-filters";
import { FACET_LABEL, facetValueLabel } from "./presentation";
import {
  type NarrowingKey,
  type PerformanceFilterState,
  type PerformanceSort,
} from "./query";
import { formatNumber } from "../../lib/format";

/**
 * The filter surface. Every option and every count comes from the server's
 * facets, which are counted over the base result, so choosing a value never
 * removes its own alternatives from the list.
 */

interface NarrowingSpec {
  key: NarrowingKey;
  facet: PerformanceFacetKey;
  anyLabel: string;
}

/**
 * The server filters behind "More filters". Actionability is not among them:
 * the actionability bar is that axis's one control. Context is offered only
 * in Everything, where more than production is listed.
 */
const NARROWING: NarrowingSpec[] = [
  { key: "context", facet: "context", anyLabel: "Any context" },
  { key: "boundary", facet: "boundary", anyLabel: "Any" },
  { key: "confidence", facet: "confidence", anyLabel: "Any" },
  { key: "proof", facet: "proof", anyLabel: "Any" },
  { key: "role", facet: "role", anyLabel: "Any" },
];

/**
 * A facet with one value is a fact, not a choice, so it is stated rather than
 * offered as a control that could only ever return the same rows.
 */
/** The label and its one value; the count belongs to the options, and one value has none to compare. */
function SingleValueFact({ facet, only }: { facet: PerformanceFacetKey; only: PerformanceFacetCount }) {
  return (
    <p className="text-xs text-[var(--color-text-tertiary)]">
      {FACET_LABEL[facet]}:{" "}
      <span className="text-[var(--color-text-secondary)]">
        {facetValueLabel(facet, only.value)}
      </span>
    </p>
  );
}

/** One server filter: a select over its facet, or a stated fact when it has one value. */
function NarrowingControl({
  spec: { key, facet, anyLabel },
  filters,
  facets,
  onChange,
}: {
  spec: NarrowingSpec;
  filters: PerformanceFilterState;
  facets: PerformanceFacets;
  onChange: (key: NarrowingKey, value: string | null) => void;
}) {
  const counts = facets[facet] ?? [];
  if (counts.length === 1 && !filters[key]) return <SingleValueFact facet={facet} only={counts[0]!} />;
  return (
    <FilterSelect
      label={FACET_LABEL[facet]}
      value={filters[key] ?? ""}
      onChange={(next) => onChange(key, next || null)}
      options={[
        { value: "", label: anyLabel },
        ...counts.map((entry) => ({
          value: entry.value,
          label: `${facetValueLabel(facet, entry.value)} (${formatNumber(entry.total)})`,
        })),
      ]}
    />
  );
}

/**
 * Scope and sort stay visible; the rest of the server filters sit behind one
 * disclosure, which states how many are active while it is closed.
 */
export function NarrowingFilters({
  filters,
  facets,
  keys,
  onChange,
}: {
  filters: PerformanceFilterState;
  facets: PerformanceFacets;
  /** The filters the current scope can narrow by. */
  keys: readonly NarrowingKey[];
  onChange: (key: NarrowingKey, value: string | null) => void;
}) {
  const specs = NARROWING.filter((spec) => keys.includes(spec.key));
  const active = specs.filter(({ key }) => filters[key] !== null).length;
  const controls = specs.filter((spec) => (facets[spec.facet] ?? []).length > 0).map((spec) => (
    <NarrowingControl key={spec.key} spec={spec} filters={filters} facets={facets} onChange={onChange} />
  ));
  if (controls.length === 0) return null;
  return <MoreFilters active={active}>{controls}</MoreFilters>;
}

/** The chip label for one active narrowing, "Boundary: Database". */
export function narrowingChipLabel(key: NarrowingKey, value: string): string {
  return `${FACET_LABEL[key]}: ${facetValueLabel(key, value)}`;
}

const SORT_OPTIONS: { value: PerformanceSort; label: string }[] = [
  { value: "rank", label: "Rank" },
  { value: "leverage", label: "Call sites" },
  { value: "observations", label: "Observations" },
];

/** How the queue is ordered. A separate axis from the narrowing filters, so
 * it gets its own control rather than sharing theirs. */
export function SortControl({
  value,
  onChange,
}: {
  value: PerformanceSort;
  onChange: (value: PerformanceSort) => void;
}) {
  return (
    <FilterSelect
      label="Sort"
      value={value}
      onChange={(next) => onChange(next as PerformanceSort)}
      options={SORT_OPTIONS}
    />
  );
}
