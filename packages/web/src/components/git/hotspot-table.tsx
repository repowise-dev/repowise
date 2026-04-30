"use client";

import { useState, useMemo } from "react";
import { TrendingUp, TrendingDown, Search, Flame, ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/shared/empty-state";
import { ChurnBar } from "./churn-bar";
import { formatLOC } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";
import type { HotspotResponse } from "@/lib/api/types";

interface HotspotTableProps {
  hotspots: HotspotResponse[];
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

export function HotspotTable({ hotspots }: HotspotTableProps) {
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

    // Client-side sort
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

  if (hotspots.length === 0) {
    return (
      <EmptyState
        title="No hotspots found"
        description="All files look stable — great work!"
      />
    );
  }

  const filters: { key: Filter; label: string; count: number }[] = [
    { key: "all", label: "All", count: hotspots.length },
    { key: "hot", label: "Hot", count: hotspots.filter((h) => h.is_hotspot).length },
    { key: "risk", label: "Bus factor risk", count: hotspots.filter((h) => h.bus_factor <= 1).length },
    { key: "accelerating", label: "Accelerating", count: hotspots.filter((h) => h.commit_count_30d * 3 > h.commit_count_90d).length },
  ];

  return (
    <div className="space-y-3">
      {/* Search + filters */}
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

      {/* Table */}
      {filtered.length === 0 ? (
        <EmptyState title="No matches" description="Try adjusting your search or filters." />
      ) : (
        <div className="rounded-lg border border-[var(--color-border-default)] overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-[var(--color-bg-elevated)]">
              <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-8">
                  #
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                  File
                </th>
                <th
                  scope="col"
                  aria-sort={ariaSortFor("commits", sortKey, sortDir)}
                  className="px-3 py-2.5 text-right text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24 cursor-pointer select-none hover:text-[var(--color-text-secondary)]"
                  onClick={() => handleSort("commits")}
                >
                  Commits 90d<SortIcon column="commits" sortKey={sortKey} sortDir={sortDir} />
                </th>
                <th
                  scope="col"
                  aria-sort={ariaSortFor("churn", sortKey, sortDir)}
                  className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-32 cursor-pointer select-none hover:text-[var(--color-text-secondary)] hidden lg:table-cell"
                  onClick={() => handleSort("churn")}
                >
                  Churn<SortIcon column="churn" sortKey={sortKey} sortDir={sortDir} />
                </th>
                <th
                  scope="col"
                  aria-sort={ariaSortFor("trend", sortKey, sortDir)}
                  className="px-3 py-2.5 text-right text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24 cursor-pointer select-none hover:text-[var(--color-text-secondary)]"
                  onClick={() => handleSort("trend")}
                  title="Exponential decay score weighting recent commits more heavily (180-day half-life)"
                >
                  Trend<SortIcon column="trend" sortKey={sortKey} sortDir={sortDir} />
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-20 hidden md:table-cell">
                  Bus Factor
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24 hidden lg:table-cell">
                  Lines ±90d
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden md:table-cell">
                  Owner
                </th>
                <th className="px-3 py-2.5 w-20" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((h, i) => {
                const accelerating = h.commit_count_30d * 3 > h.commit_count_90d;
                const trendScore = h.temporal_hotspot_score;
                return (
                  <tr
                    key={h.file_path}
                    className="border-b border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)] transition-colors last:border-0"
                  >
                    <td className="px-3 py-2.5 text-[var(--color-text-tertiary)] tabular-nums text-xs">
                      {i + 1}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-[var(--color-text-primary)] min-w-[220px] max-w-[520px]">
                      <span className="block truncate" title={h.file_path}>{h.file_path}</span>
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-xs text-right">
                      <span className="inline-flex items-center justify-end gap-1">
                        <span className="text-[var(--color-text-secondary)]">
                          {h.commit_count_90d}
                        </span>
                        {accelerating ? (
                          <TrendingUp className="h-3 w-3 text-red-500" />
                        ) : (
                          <TrendingDown className="h-3 w-3 text-green-500" />
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
                            <Flame className={cn("h-3 w-3 shrink-0", trendScore >= 5 ? "text-red-500" : trendScore >= 2 ? "text-orange-400" : "text-[var(--color-text-tertiary)]")} />
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
                            ? "bg-red-500/15 text-red-400"
                            : h.bus_factor === 2
                              ? "bg-yellow-500/15 text-yellow-400"
                              : "bg-green-500/15 text-green-400"
                        }`}
                      >
                        {h.bus_factor}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-xs tabular-nums hidden lg:table-cell">
                      <span className="text-green-400">+{formatLOC(h.lines_added_90d)}</span>
                      {" "}
                      <span className="text-red-400">-{formatLOC(h.lines_deleted_90d)}</span>
                    </td>
                    <td className="px-3 py-2.5 text-xs text-[var(--color-text-secondary)] hidden md:table-cell">
                      {h.primary_owner ?? "—"}
                    </td>
                    <td className="px-3 py-2.5 flex items-center gap-1">
                      {h.is_hotspot && <Badge variant="outdated">Hot</Badge>}
                      {h.is_stable && <Badge variant="fresh">Stable</Badge>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
