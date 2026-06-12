"use client";

import { useState, useMemo } from "react";
import { Search, GitBranch, Flame } from "lucide-react";
import { Badge } from "../ui/badge";
import { Input } from "../ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import { RowActions } from "../shared/row-actions";
import { cn } from "../lib/cn";
import type { OwnershipEntry } from "@repowise-dev/types/git";

interface OwnershipTableProps {
  entries: OwnershipEntry[];
  repoId?: string;
  linkPrefix?: string;
}

type Filter = "all" | "silo";

export function OwnershipTable({ entries, repoId, linkPrefix }: OwnershipTableProps) {
  const prefix = linkPrefix ?? (repoId ? `/repos/${repoId}` : undefined);
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

  const columns: ResponsiveColumn<OwnershipEntry>[] = [
    {
      key: "module_path",
      header: "Module / File",
      priority: 1,
      cellClassName: "font-mono text-xs text-[var(--color-text-primary)] min-w-[160px] max-w-[480px]",
      render: (entry) => (
        <span className="block truncate" title={entry.module_path}>
          {entry.module_path}
        </span>
      ),
    },
    {
      key: "owner",
      header: "Owner",
      priority: 2,
      cellClassName: "text-xs text-[var(--color-text-secondary)] max-w-[200px]",
      render: (entry) => (
        <span className="block truncate" title={entry.primary_owner ?? undefined}>
          {entry.primary_owner ?? "—"}
        </span>
      ),
    },
    {
      key: "ownership",
      header: "Ownership",
      priority: 2,
      headerClassName: "w-36",
      render: (entry) =>
        entry.owner_pct !== null ? (
          <div className="flex items-center gap-2 min-w-[100px]">
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
        ),
      mobileRender: (entry) =>
        entry.owner_pct !== null ? `${Math.round(entry.owner_pct * 100)}%` : null,
    },
    {
      key: "files",
      header: "Files",
      priority: 3,
      cellClassName: "text-xs text-[var(--color-text-tertiary)] tabular-nums",
      render: (entry) => entry.file_count,
    },
    {
      key: "actions",
      header: "",
      priority: 1,
      headerClassName: "w-20",
      render: (entry) => (
        <div className="flex items-center gap-1">
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
          {prefix && (
            <RowActions
              actions={[
                { icon: GitBranch, label: "Graph", href: `${prefix}/graph?node=${encodeURIComponent(entry.module_path)}` },
                { icon: Flame, label: "Hotspots", href: `${prefix}/code-health?tab=hotspots` },
              ]}
            />
          )}
        </div>
      ),
      mobileRender: (entry) => (entry.is_silo ? <Badge variant="stale">Silo</Badge> : null),
    },
  ];

  return (
    <div className="space-y-3">
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

      <ResponsiveTable
        columns={columns}
        rows={filtered}
        rowKey={(entry) => entry.module_path}
        empty={<EmptyState title="No matches" description="Try adjusting your search or filters." />}
      />
    </div>
  );
}
