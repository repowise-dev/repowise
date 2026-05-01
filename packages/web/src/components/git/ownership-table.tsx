"use client";

import { useState, useMemo } from "react";
import { Search } from "lucide-react";
import { Badge } from "@repowise/ui/ui/badge";
import { Input } from "@repowise/ui/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@repowise/ui/ui/tooltip";
import { EmptyState } from "@/components/shared/empty-state";
import { cn } from "@/lib/utils/cn";
import type { OwnershipEntry } from "@/lib/api/types";

interface OwnershipTableProps {
  entries: OwnershipEntry[];
}

type Filter = "all" | "silo";

export function OwnershipTable({ entries }: OwnershipTableProps) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const filtered = useMemo(() => {
    let items = entries;

    if (search) {
      const q = search.toLowerCase();
      items = items.filter(
        (e) =>
          e.module_path.toLowerCase().includes(q) ||
          (e.primary_owner ?? "").toLowerCase().includes(q),
      );
    }

    if (filter === "silo") {
      items = items.filter((e) => e.is_silo);
    }

    return items;
  }, [entries, search, filter]);

  if (entries.length === 0) {
    return (
      <EmptyState
        title="No ownership data"
        description="Run a sync to populate ownership information."
      />
    );
  }

  const siloCount = entries.filter((e) => e.is_silo).length;

  return (
    <div className="space-y-3">
      {/* Search + filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
          <Input
            placeholder="Search modules or owners…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 h-8 w-full sm:w-56 text-xs"
            aria-label="Search modules or owners"
          />
        </div>
        <div className="flex rounded-md border border-[var(--color-border-default)] overflow-hidden text-xs">
          <button
            onClick={() => setFilter("all")}
            className={cn(
              "px-2.5 py-1.5 font-medium transition-colors",
              filter === "all"
                ? "bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
                : "bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
            )}
          >
            All <span className="text-[10px] opacity-70">({entries.length})</span>
          </button>
          <button
            onClick={() => setFilter("silo")}
            className={cn(
              "px-2.5 py-1.5 font-medium transition-colors",
              filter === "silo"
                ? "bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
                : "bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
            )}
          >
            Silos <span className="text-[10px] opacity-70">({siloCount})</span>
          </button>
        </div>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          {filtered.length} {filtered.length === 1 ? "entry" : "entries"}
        </span>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title="No matches" description="Try adjusting your search or filters." />
      ) : (
        <div className="rounded-lg border border-[var(--color-border-default)] overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-[var(--color-bg-elevated)]">
              <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                  Module / File
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                  Owner
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-36">
                  Ownership
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-16">
                  Files
                </th>
                <th className="px-4 py-2.5 w-20" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((entry) => (
                <tr
                  key={entry.module_path}
                  className="border-b border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)] transition-colors last:border-0"
                >
                  <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-primary)] min-w-[200px] max-w-[480px]">
                    <span className="block truncate" title={entry.module_path}>{entry.module_path}</span>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-[var(--color-text-secondary)] max-w-[200px]">
                    <span className="block truncate" title={entry.primary_owner ?? undefined}>
                      {entry.primary_owner ?? "—"}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    {entry.owner_pct !== null ? (
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 flex-1 rounded-full bg-[var(--color-bg-elevated)]">
                          <div
                            className="h-1.5 rounded-full bg-[var(--color-accent-primary)]"
                            style={{ width: `${Math.min(100, entry.owner_pct * 100)}%` }}
                          />
                        </div>
                        <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8">
                          {Math.round(entry.owner_pct * 100)}%
                        </span>
                      </div>
                    ) : (
                      <span className="text-[var(--color-text-tertiary)]">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-[var(--color-text-tertiary)] tabular-nums">
                    {entry.file_count}
                  </td>
                  <td className="px-4 py-2.5">
                    {entry.is_silo && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span>
                            <Badge variant="stale">Silo</Badge>
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>Bus factor risk</TooltipContent>
                      </Tooltip>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
