"use client";

import * as React from "react";
import { useMemo } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import type {
  Hotspot,
  HotspotFacets,
  HotspotFilter,
  HotspotOrder,
  HotspotSort,
} from "@repowise-dev/types/git";
import { EmptyState } from "../shared/empty-state";
import { ResultsFooter } from "../shared/results-footer";
import { useVirtualRows } from "../shared/virtualized-table";
import { HotspotRow } from "./hotspot-row";
import { HotspotToolbar } from "./hotspot-toolbar";

/** Row height (px); rows are one line, so the estimate is the height. */
const ROW_HEIGHT = 44;

export interface HotspotSortState {
  key: HotspotSort;
  order: HotspotOrder;
}

export const DEFAULT_HOTSPOT_SORT: HotspotSortState = { key: "score", order: "desc" };

interface HotspotTableProps {
  /** The loaded rows, in the server's order. */
  hotspots: Hotspot[];
  /** Rows the current filter and search match (server count). */
  total?: number;
  /** Server counts per filter over the searched set; chips show only with these. */
  facets?: HotspotFacets;
  query?: string;
  /** Called with the settled search (debounced here); the host fetches. */
  onQueryChange?: (q: string) => void;
  filter?: HotspotFilter;
  onFilterChange?: (filter: HotspotFilter) => void;
  sort?: HotspotSortState;
  /** Sortable headers render only when the host can sort on the server. */
  onSortChange?: (sort: HotspotSortState) => void;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  /** Row click: the host opens its file card. */
  onSelect?: (hotspot: Hotspot) => void;
  /** The row's agent prompt (quiet, on hover/focus). */
  onGeneratePrompt?: (hotspot: Hotspot) => void;
  /** One prompt over the listed rows ("Copy prompt for these N"). */
  onGenerateBatchPrompt?: (rows: Hotspot[]) => void;
  /** The header prompt's label; English "Copy prompt for these N" by default. */
  batchPromptLabel?: (count: number) => string;
  /**
   * Accepted and ignored, for callers that still pass them: the row's own
   * click opens the file card, which already lists the file's top symbols and
   * links, so a per-row menu and an inline symbols expand were competing
   * actions. Remove from hosts when they next port.
   */
  repoId?: string;
  linkPrefix?: string;
  renderExpandedRow?: (hotspot: Hotspot) => React.ReactNode;
}

function SortHeader({
  column,
  label,
  title,
  sort,
  onSortChange,
  align = "right",
  className,
}: {
  column: HotspotSort;
  label: string;
  title?: string;
  sort: HotspotSortState;
  onSortChange?: ((s: HotspotSortState) => void) | undefined;
  align?: "left" | "right";
  className?: string;
}) {
  const active = sort.key === column;
  const ariaSort = active ? (sort.order === "asc" ? "ascending" : "descending") : "none";
  const Icon = !active ? ArrowUpDown : sort.order === "desc" ? ArrowDown : ArrowUp;
  return (
    <th
      scope="col"
      aria-sort={onSortChange ? ariaSort : undefined}
      title={title}
      className={`px-3 py-2.5 ${align === "left" ? "text-left" : "text-right"} text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider ${className ?? ""}`}
    >
      {onSortChange ? (
        <button
          type="button"
          className="cursor-pointer select-none uppercase tracking-wider font-medium hover:text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] rounded"
          onClick={() =>
            onSortChange({
              key: column,
              order: active && sort.order === "desc" ? "asc" : "desc",
            })
          }
        >
          {label}
          <Icon className={`inline h-3 w-3 ml-1 ${active ? "" : "opacity-60"}`} />
        </button>
      ) : (
        label
      )}
    </th>
  );
}

const TH = "px-3 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider";

export function HotspotTable({
  hotspots,
  total,
  facets,
  query = "",
  onQueryChange,
  filter = "all",
  onFilterChange,
  sort = DEFAULT_HOTSPOT_SORT,
  onSortChange,
  hasMore,
  loadingMore,
  onLoadMore,
  onSelect,
  onGeneratePrompt,
  onGenerateBatchPrompt,
  batchPromptLabel,
}: HotspotTableProps) {
  const listed = total ?? hotspots.length;
  // Churn and bug-fix history are different claims, so the Fixes column only
  // appears once the index carries fix data for this set of files: anywhere in
  // the searched set (the facet), not only on the loaded page.
  const showFixes = useMemo(
    () => (facets?.with_fixes ?? 0) > 0 || hotspots.some((h) => (h.prior_defect_count ?? 0) > 0),
    [facets, hotspots],
  );
  const { scrollRef, virtualRows, paddingTop, paddingBottom } = useVirtualRows({
    count: hotspots.length,
    estimateSize: ROW_HEIGHT,
  });

  const narrowed = query !== "" || filter !== "all";
  if (hotspots.length === 0 && !narrowed) {
    return <EmptyState title="No hotspots found" description="No file stands out for recent change." />;
  }

  return (
    <div className="space-y-3">
      <HotspotToolbar
        query={query}
        onQueryChange={onQueryChange}
        filter={filter}
        onFilterChange={onFilterChange}
        facets={facets}
        total={listed}
        onPromptForRows={onGenerateBatchPrompt ? () => onGenerateBatchPrompt(hotspots) : undefined}
        batchPromptLabel={batchPromptLabel}
      />

      {hotspots.length === 0 ? (
        <EmptyState title="No matches" description="Try another search or filter." />
      ) : (
        <div
          ref={scrollRef}
          className="border border-[var(--color-border-default)] overflow-auto"
          style={{ maxHeight: 600 }}
        >
          <table className="w-full min-w-[760px] text-sm">
            <thead className="sticky top-0 z-10 bg-[var(--color-bg-surface)]">
              <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
                <th scope="col" className={`${TH} w-10`} title="Place by hotspot score">
                  #
                </th>
                <th scope="col" className={TH}>
                  File
                </th>
                <SortHeader column="commits" label="Commits 90d" sort={sort} onSortChange={onSortChange} className="w-28" />
                {showFixes && (
                  <SortHeader
                    column="fixes"
                    label="Fixes 6mo"
                    title="Bug-fix commits that touched this file in the last 6 months. Churn counts every change; this counts only the ones that fixed something."
                    sort={sort}
                    onSortChange={onSortChange}
                    className="w-24"
                  />
                )}
                <SortHeader
                  column="churn"
                  label="Churn"
                  align="left"
                  sort={sort}
                  onSortChange={onSortChange}
                  className="w-32 hidden lg:table-cell"
                />
                <SortHeader
                  column="score"
                  label="Score"
                  title="Hotspot score: commits weighted by recency (180-day half-life). The ranking."
                  sort={sort}
                  onSortChange={onSortChange}
                  className="w-24"
                />
                <th scope="col" className={`${TH} w-20 hidden md:table-cell`}>
                  Bus factor
                </th>
                <th scope="col" className={`${TH} w-24 hidden lg:table-cell`}>
                  Lines ±90d
                </th>
                <th scope="col" className={`${TH} hidden md:table-cell`}>
                  Owner
                </th>
                <th scope="col" className="px-3 py-2.5 w-0">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {paddingTop > 0 && (
                <tr aria-hidden>
                  <td style={{ height: paddingTop, padding: 0, border: 0 }} />
                </tr>
              )}
              {virtualRows.map((vr) => {
                const h = hotspots[vr.index];
                if (h === undefined) return null;
                return (
                  <HotspotRow
                    key={h.file_path}
                    hotspot={h}
                    rank={h.rank ?? vr.index + 1}
                    showFixes={showFixes}
                    onSelect={onSelect}
                    onGeneratePrompt={onGeneratePrompt}
                  />
                );
              })}
              {paddingBottom > 0 && (
                <tr aria-hidden>
                  <td style={{ height: paddingBottom, padding: 0, border: 0 }} />
                </tr>
              )}
            </tbody>
          </table>
          <ResultsFooter
            shown={hotspots.length}
            total={listed}
            hasMore={!!hasMore}
            loading={loadingMore}
            onLoadMore={onLoadMore}
            noun="hotspots"
          />
        </div>
      )}
    </div>
  );
}
