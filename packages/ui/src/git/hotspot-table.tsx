"use client";

import * as React from "react";
import { useState, useMemo } from "react";
import { TrendingUp, TrendingDown, Search, Flame, ArrowUpDown, ArrowUp, ArrowDown, GitBranch, BookOpen, Radius, ChevronRight, ChevronDown } from "lucide-react";
import { Badge } from "../ui/badge";
import { Input } from "../ui/input";
import { EmptyState } from "../shared/empty-state";
import { ResultsFooter } from "../shared/results-footer";
import { RowActions } from "../shared/row-actions";
import { AiPromptButton } from "../health/ai-prompt-button";
import { ChurnBar } from "./churn-bar";
import { formatLOC } from "../lib/format";
import { cn } from "../lib/cn";
import { useVirtualRows } from "../shared/virtualized-table";
import { clickableRowProps, CLICKABLE_ROW_CLS } from "../shared/responsive-table";
import type { Hotspot } from "@repowise-dev/types/git";

/**
 * Collapsed main-row height (px), used as the initial virtualization estimate.
 * Real heights — including expanded detail rows — are measured at runtime via
 * `measureElement`, so this only affects the first paint and off-screen spacers.
 */
const ESTIMATED_ROW_HEIGHT = 44;

interface HotspotTableProps {
  hotspots: Hotspot[];
  repoId?: string;
  linkPrefix?: string;
  /**
   * Optional row-click handler. When set, the entire row becomes clickable and
   * the host can render a FileCardDialog (or similar) populated from the
   * passed Hotspot. The action buttons in the rightmost column are still
   * clickable independently — they stop propagation.
   */
  onSelect?: (hotspot: Hotspot) => void;
  /**
   * Pagination signals. When `total` is provided, the table renders a
   * "Showing N of M / Load more" footer so the user can see whether the
   * data was truncated. `onLoadMore` is wired to the parent's fetch — the
   * table itself stays presentational.
   */
  total?: number;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  /**
   * When provided, each row shows a chevron toggle that expands an inline
   * panel below it — used for hotspot → "top symbols in this file" drill-down.
   * The host owns data-fetching and renders the panel body.
   */
  renderExpandedRow?: (hotspot: Hotspot) => React.ReactNode;
  /** When set, each row shows an "AI stabilize" action that calls this. */
  onGeneratePrompt?: (hotspot: Hotspot) => void;
}

type Filter = "all" | "hot" | "risk" | "accelerating";
type SortKey = "trend" | "churn" | "commits";
type SortDir = "asc" | "desc";

function SortIcon({ column, sortKey, sortDir }: { column: SortKey; sortKey: SortKey; sortDir: SortDir }) {
  if (column !== sortKey) return <ArrowUpDown className="inline h-3 w-3 ml-1 opacity-60" />;
  return sortDir === "desc"
    ? <ArrowDown className="inline h-3 w-3 ml-1" />
    : <ArrowUp className="inline h-3 w-3 ml-1" />;
}

function ariaSortFor(column: SortKey, sortKey: SortKey, sortDir: SortDir): "none" | "ascending" | "descending" {
  if (column !== sortKey) return "none";
  return sortDir === "asc" ? "ascending" : "descending";
}

export function HotspotTable({
  hotspots,
  repoId,
  linkPrefix,
  onSelect,
  total,
  hasMore,
  loadingMore,
  onLoadMore,
  renderExpandedRow,
  onGeneratePrompt,
}: HotspotTableProps) {
  const expandable = !!renderExpandedRow;
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const toggleExpand = (filePath: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(filePath)) next.delete(filePath);
      else next.add(filePath);
      return next;
    });
  };
  const prefix = linkPrefix ?? (repoId ? `/repos/${repoId}` : undefined);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("trend");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const filtered = useMemo(() => {
    let items = hotspots;

    if (search) {
      const q = search.toLowerCase();
      items = items.filter(
        (h) =>
          h.file_path.toLowerCase().includes(q) ||
          (h.primary_owner ?? "").toLowerCase().includes(q),
      );
    }

    switch (filter) {
      case "hot":
        items = items.filter((h) => h.is_hotspot);
        break;
      case "risk":
        items = items.filter((h) => h.bus_factor <= 1);
        break;
      case "accelerating":
        items = items.filter((h) => h.commit_count_30d * 3 > h.commit_count_90d);
        break;
    }

    const sign = sortDir === "desc" ? -1 : 1;
    items = [...items].sort((a, b) => {
      if (sortKey === "trend") {
        const av = a.temporal_hotspot_score ?? -1;
        const bv = b.temporal_hotspot_score ?? -1;
        return sign * (av - bv);
      }
      if (sortKey === "churn") return sign * (a.churn_percentile - b.churn_percentile);
      if (sortKey === "commits") return sign * (a.commit_count_90d - b.commit_count_90d);
      return 0;
    });

    return items;
  }, [hotspots, search, filter, sortKey, sortDir]);

  // Memoize the filter chips' counts so the three full `hotspots` scans only
  // run when the dataset changes, not on every keystroke / sort / expand.
  const filters: { key: Filter; label: string; count: number }[] = useMemo(
    () => [
      { key: "all", label: "All", count: hotspots.length },
      { key: "hot", label: "Hot", count: hotspots.filter((h) => h.is_hotspot).length },
      { key: "risk", label: "Bus factor risk", count: hotspots.filter((h) => h.bus_factor <= 1).length },
      { key: "accelerating", label: "Accelerating", count: hotspots.filter((h) => h.commit_count_30d * 3 > h.commit_count_90d).length },
    ],
    [hotspots],
  );

  // Window the tbody rows. Rows are variable-height (an expanded row adds a
  // second <tr>), so we drive the windowing with the lower-level hook and let
  // it MEASURE real heights via `measureElement`. To capture the *combined*
  // height of a logical row (main <tr> + optional expanded <tr>) within valid
  // table semantics, each logical row is rendered as its own <tbody> — a
  // <table> may contain multiple <tbody>s — and the measured/`data-index`d
  // element is that <tbody>, so an expanded row's full height is tracked.
  const {
    scrollRef,
    virtualRows,
    paddingTop,
    paddingBottom,
    measureElement,
  } = useVirtualRows({
    count: filtered.length,
    estimateSize: ESTIMATED_ROW_HEIGHT,
  });

  if (hotspots.length === 0) {
    return (
      <EmptyState
        title="No hotspots found"
        description="All files look stable — great work!"
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
          <Input
            placeholder="Search files or owners…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 h-8 w-full sm:w-56 text-xs"
          />
        </div>
        <div className="flex flex-wrap rounded-md border border-[var(--color-border-default)] overflow-hidden text-xs">
          {filters.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={cn(
                "px-2.5 py-1.5 font-medium transition-colors",
                filter === f.key
                  ? "bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
                  : "bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
              )}
            >
              {f.label}
              <span className="ml-1 text-[10px] opacity-70">({f.count})</span>
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title="No matches" description="Try adjusting your search or filters." />
      ) : (
        <div
          ref={scrollRef}
          className="border border-[var(--color-border-default)] overflow-auto"
          style={{ maxHeight: 600 }}
        >
          <table className="w-full min-w-[760px] text-sm">
            <thead className="sticky top-0 z-10 bg-[var(--color-bg-surface)]">
              <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
                {expandable && <th className="w-6 px-1" aria-hidden="true" />}
                <th className="px-3 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-8">
                  #
                </th>
                <th className="px-3 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                  File
                </th>
                <th
                  scope="col"
                  aria-sort={ariaSortFor("commits", sortKey, sortDir)}
                  className="px-3 py-2.5 text-right text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24"
                >
                  <button
                    type="button"
                    className="cursor-pointer select-none uppercase tracking-wider font-medium hover:text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] rounded"
                    onClick={() => handleSort("commits")}
                  >
                    Commits 90d<SortIcon column="commits" sortKey={sortKey} sortDir={sortDir} />
                  </button>
                </th>
                <th
                  scope="col"
                  aria-sort={ariaSortFor("churn", sortKey, sortDir)}
                  className="px-3 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-32 hidden lg:table-cell"
                >
                  <button
                    type="button"
                    className="cursor-pointer select-none uppercase tracking-wider font-medium hover:text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] rounded"
                    onClick={() => handleSort("churn")}
                  >
                    Churn<SortIcon column="churn" sortKey={sortKey} sortDir={sortDir} />
                  </button>
                </th>
                <th
                  scope="col"
                  aria-sort={ariaSortFor("trend", sortKey, sortDir)}
                  className="px-3 py-2.5 text-right text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24"
                >
                  <button
                    type="button"
                    className="cursor-pointer select-none uppercase tracking-wider font-medium hover:text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] rounded"
                    onClick={() => handleSort("trend")}
                    title="Exponential decay score weighting recent commits more heavily (180-day half-life)"
                  >
                    Trend<SortIcon column="trend" sortKey={sortKey} sortDir={sortDir} />
                  </button>
                </th>
                <th className="px-3 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-20 hidden md:table-cell">
                  Bus Factor
                </th>
                <th className="px-3 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24 hidden lg:table-cell">
                  Lines ±90d
                </th>
                <th className="px-3 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden md:table-cell">
                  Owner
                </th>
                <th className="px-3 py-2.5 w-20" />
              </tr>
            </thead>
            {paddingTop > 0 && (
              <tbody aria-hidden>
                <tr>
                  <td style={{ height: paddingTop, padding: 0, border: 0 }} />
                </tr>
              </tbody>
            )}
            {virtualRows.map((vr) => {
              const h = filtered[vr.index];
              if (h === undefined) return null;
              const i = vr.index;
              const accelerating = h.commit_count_30d * 3 > h.commit_count_90d;
              const trendScore = h.temporal_hotspot_score;
              const isExpanded = expanded.has(h.file_path);
              return (
                <tbody key={h.file_path} ref={measureElement} data-index={i}>
                  <tr
                    {...(onSelect ? clickableRowProps(() => onSelect(h)) : {})}
                    className={cn(
                      "border-b border-[var(--color-table-divider)] hover:bg-[var(--color-bg-elevated)] transition-colors group",
                      !isExpanded && "last:border-0",
                      onSelect && CLICKABLE_ROW_CLS,
                    )}
                  >
                    {expandable && (
                      <td
                        className="px-1 py-2.5 align-middle"
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleExpand(h.file_path);
                        }}
                      >
                        <button
                          type="button"
                          aria-label={isExpanded ? "Collapse symbols" : "Expand symbols"}
                          aria-expanded={isExpanded}
                          className="flex h-5 w-5 items-center justify-center rounded text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
                        >
                          {isExpanded ? (
                            <ChevronDown className="h-3.5 w-3.5" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </td>
                    )}
                    <td className="px-3 py-2.5 text-[var(--color-text-tertiary)] tabular-nums text-xs">
                      {i + 1}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-[var(--color-text-primary)] min-w-[180px] max-w-[420px]">
                      <span className="block truncate group-hover:underline underline-offset-2" title={h.file_path}>{h.file_path}</span>
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-xs text-right">
                      <span className="inline-flex items-center justify-end gap-1">
                        <span className="text-[var(--color-text-secondary)]">
                          {h.commit_count_90d}
                        </span>
                        {accelerating ? (
                          <TrendingUp className="h-3 w-3 text-[var(--color-error)]" />
                        ) : (
                          <TrendingDown className="h-3 w-3 text-[var(--color-success)]" />
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 hidden lg:table-cell">
                      <div className="flex items-center gap-2">
                        <ChurnBar percentile={h.churn_percentile} className="w-16" />
                        <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8">
                          {Math.round(h.churn_percentile)}%
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-xs text-right">
                      <span className="inline-flex items-center justify-end gap-1">
                        {trendScore != null ? (
                          <>
                            <Flame className={cn("h-3 w-3 shrink-0", trendScore >= 5 ? "text-[var(--color-error)]" : trendScore >= 2 ? "text-[var(--color-warning)]" : "text-[var(--color-text-tertiary)]")} />
                            <span className="text-[var(--color-text-secondary)]">
                              {trendScore.toFixed(2)}
                            </span>
                          </>
                        ) : (
                          <span className="text-[var(--color-text-tertiary)]">—</span>
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 hidden md:table-cell">
                      <span
                        className={`inline-flex items-center justify-center rounded px-1.5 py-0.5 text-xs font-medium tabular-nums ${
                          h.bus_factor <= 1
                            ? "bg-[var(--color-error)]/15 text-[var(--color-error)]"
                            : h.bus_factor === 2
                              ? "bg-[var(--color-warning)]/15 text-[var(--color-warning)]"
                              : "bg-[var(--color-success)]/15 text-[var(--color-success)]"
                        }`}
                      >
                        {h.bus_factor}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-xs tabular-nums hidden lg:table-cell">
                      <span className="text-[var(--color-success)]">+{formatLOC(h.lines_added_90d)}</span>
                      {" "}
                      <span className="text-[var(--color-error)]">-{formatLOC(h.lines_deleted_90d)}</span>
                    </td>
                    <td className="px-3 py-2.5 text-xs text-[var(--color-text-secondary)] hidden md:table-cell">
                      {h.primary_owner ?? "—"}
                    </td>
                    <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center gap-1">
                        {h.is_hotspot && <Badge variant="outdated">Hot</Badge>}
                        {h.is_stable && <Badge variant="fresh">Stable</Badge>}
                        {onGeneratePrompt && (
                          <AiPromptButton
                            variant="icon"
                            label="AI stabilization prompt"
                            onClick={() => onGeneratePrompt(h)}
                          />
                        )}
                        {prefix && (
                          <RowActions
                            actions={[
                              { icon: GitBranch, label: "Graph", href: `${prefix}/architecture?view=graph&node=${encodeURIComponent(h.file_path)}` },
                              { icon: BookOpen, label: "Docs", href: `${prefix}/docs?file=${encodeURIComponent(h.file_path)}` },
                              { icon: Radius, label: "Blast Radius", href: `${prefix}/code-health?tab=impact&file=${encodeURIComponent(h.file_path)}` },
                            ]}
                          />
                        )}
                      </div>
                    </td>
                  </tr>
                  {expandable && isExpanded && (
                    <tr className="border-b border-[var(--color-table-divider)] bg-[var(--color-bg-subtle)] last:border-0">
                      <td className="px-1" />
                      <td colSpan={9} className="px-3 py-3">
                        {renderExpandedRow!(h)}
                      </td>
                    </tr>
                  )}
                </tbody>
              );
            })}
            {paddingBottom > 0 && (
              <tbody aria-hidden>
                <tr>
                  <td style={{ height: paddingBottom, padding: 0, border: 0 }} />
                </tr>
              </tbody>
            )}
          </table>
          {total != null && (
            <ResultsFooter
              shown={filtered.length}
              total={total}
              hasMore={!!hasMore}
              loading={loadingMore}
              onLoadMore={onLoadMore}
              noun="hotspots"
            />
          )}
        </div>
      )}
    </div>
  );
}
