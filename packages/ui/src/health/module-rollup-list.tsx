"use client";

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
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
  const toggle = (key: SortKey) => {
    if (sort === key) setOrder((o) => (o === "asc" ? "desc" : "asc"));
    else {
      setSort(key);
      setOrder(key === "average_health" ? "asc" : "desc");
    }
  };

  return (
    <div className="border border-[var(--color-border-default)] overflow-hidden">
      <table className="w-full text-sm">
        <thead className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)] text-[var(--color-text-tertiary)] text-[11px] uppercase tracking-wider">
          <tr>
            <Th label="Module" active={sort === "module"} order={order} onClick={() => toggle("module")} />
            <Th label="Files" active={sort === "file_count"} order={order} onClick={() => toggle("file_count")} />
            <Th label="NLOC" active={sort === "nloc"} order={order} onClick={() => toggle("nloc")} />
            <Th label="Avg health" active={sort === "average_health"} order={order} onClick={() => toggle("average_health")} />
            <th className="text-left px-3 py-2">Worst file</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((m) => (
            <tr
              key={m.module}
              className={`group border-t border-[var(--color-table-divider)] ${onSelect ? "cursor-pointer hover:bg-[var(--color-bg-elevated)]" : ""}`}
              onClick={onSelect ? () => onSelect(m) : undefined}
            >
              <td className="px-3 py-2 font-medium text-[var(--color-text-primary)]">
                <span className="group-hover:underline underline-offset-2">{m.module}</span>
              </td>
              <td className="px-3 py-2 tabular-nums text-[var(--color-text-secondary)]">{m.file_count}</td>
              <td className="px-3 py-2 tabular-nums text-[var(--color-text-secondary)]">{m.nloc.toLocaleString()}</td>
              <td className="px-3 py-2">
                <HealthBadge score={m.average_health} />
              </td>
              <td className="px-3 py-2 font-mono text-xs text-[var(--color-text-secondary)] truncate max-w-[280px]" title={m.worst_performer_path}>
                {m.worst_performer_path}{" "}
                <span className="text-[var(--color-text-tertiary)]">({m.worst_performer_score.toFixed(1)})</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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

function Th({
  label,
  active,
  order,
  onClick,
}: {
  label: string;
  active: boolean;
  order: "asc" | "desc";
  onClick: () => void;
}) {
  return (
    <th
      className="text-left px-3 py-2 font-medium cursor-pointer select-none"
      onClick={onClick}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active ? (order === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />) : null}
      </span>
    </th>
  );
}
