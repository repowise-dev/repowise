"use client";

import { useMemo, useState } from "react";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { HealthBadge } from "./health-badge";

export interface ModuleRollupRow {
  module: string;
  file_count: number;
  nloc: number;
  average_health: number;
  worst_performer_path: string;
  worst_performer_score: number;
}

export interface ModuleRollupListProps {
  modules: ModuleRollupRow[];
  emptyMessage?: string;
  onSelect?: (row: ModuleRollupRow) => void;
  pageSize?: number;
}

type SortKey = "average_health" | "file_count" | "nloc" | "module";

const SORT_KEYS: SortKey[] = ["average_health", "file_count", "nloc", "module"];

const COLUMNS: ResponsiveColumn<ModuleRollupRow>[] = [
  {
    key: "module",
    header: "Module",
    sortable: true,
    render: (m) => (
      <span className="font-medium text-[var(--color-text-primary)]">{m.module}</span>
    ),
  },
  {
    key: "file_count",
    header: "Files",
    sortable: true,
    render: (m) => (
      <span className="tabular-nums text-[var(--color-text-secondary)]">{m.file_count}</span>
    ),
  },
  {
    key: "nloc",
    header: "NLOC",
    sortable: true,
    priority: 2,
    render: (m) => (
      <span className="tabular-nums text-[var(--color-text-secondary)]">
        {m.nloc.toLocaleString()}
      </span>
    ),
  },
  {
    key: "average_health",
    header: "Avg health",
    sortable: true,
    render: (m) => <HealthBadge score={m.average_health} />,
  },
  {
    key: "worst_performer_path",
    header: "Worst file",
    priority: 3,
    render: (m) => (
      <span
        className="block truncate max-w-[280px] font-mono text-xs text-[var(--color-text-secondary)]"
        title={m.worst_performer_path}
      >
        {m.worst_performer_path}{" "}
        <span className="text-[var(--color-text-tertiary)]">
          ({m.worst_performer_score.toFixed(1)})
        </span>
      </span>
    ),
  },
];

export function ModuleRollupList({
  modules,
  emptyMessage = "No modules detected yet. Community labels populate after the first index.",
  onSelect,
  pageSize = 15,
}: ModuleRollupListProps) {
  const [sort, setSort] = useState<SortKey>("average_health");
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [expanded, setExpanded] = useState(false);

  const sorted = useMemo(() => {
    const copy = [...modules];
    copy.sort((a, b) => {
      const av = a[sort] as number | string;
      const bv = b[sort] as number | string;
      if (typeof av === "string" && typeof bv === "string") {
        return order === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      return order === "asc" ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });
    return copy;
  }, [modules, sort, order]);

  if (!modules || modules.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)]">
        {emptyMessage}
      </div>
    );
  }

  const visible = expanded ? sorted : sorted.slice(0, pageSize);
  const toggle = (key: string) => {
    if (!SORT_KEYS.includes(key as SortKey)) return;
    if (sort === key) setOrder((o) => (o === "asc" ? "desc" : "asc"));
    else {
      setSort(key as SortKey);
      setOrder(key === "average_health" ? "asc" : "desc");
    }
  };

  return (
    <div className="border border-[var(--color-border-default)] overflow-hidden">
      <ResponsiveTable
        columns={COLUMNS}
        rows={visible}
        rowKey={(m) => m.module}
        caption="Module health rollup"
        sortField={sort}
        sortOrder={order}
        onSort={toggle}
        {...(onSelect ? { onRowClick: onSelect } : {})}
        stacked="sm"
        bare
      />
      {modules.length > pageSize ? (
        <div className="border-t border-[var(--color-border-default)] px-3 py-2 text-xs text-[var(--color-text-tertiary)]">
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="text-[var(--color-accent-primary)] hover:underline"
          >
            {expanded ? "Show fewer" : `Show all ${modules.length}`}
          </button>
        </div>
      ) : null}
    </div>
  );
}
