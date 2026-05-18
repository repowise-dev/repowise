"use client";

import { ArrowDown, ArrowUp, FileText } from "lucide-react";
import { scoreBadgeClass } from "./tokens";

export interface HealthFileRow {
  file_path: string;
  score: number;
  max_ccn: number;
  max_nesting: number;
  nloc: number;
  has_test_file: boolean;
  line_coverage_pct?: number | null;
  module?: string | null;
  duplication_pct?: number | null;
}

export type FileSortField =
  | "score"
  | "max_ccn"
  | "max_nesting"
  | "nloc"
  | "duplication_pct"
  | "line_coverage_pct"
  | "file_path";

export interface HealthFileTableProps {
  files: HealthFileRow[];
  sortField?: FileSortField;
  sortOrder?: "asc" | "desc";
  onSort?: (field: FileSortField) => void;
  onSelect?: (row: HealthFileRow) => void;
  selectedPath?: string | null;
  emptyMessage?: string;
}

interface ColDef {
  key: FileSortField | "tests";
  label: string;
  align: "left" | "right" | "center";
  sortable: boolean;
  className?: string;
}

const COLS: ColDef[] = [
  { key: "file_path", label: "File", align: "left", sortable: true },
  { key: "score", label: "Score", align: "right", sortable: true },
  { key: "max_ccn", label: "CCN", align: "right", sortable: true },
  { key: "max_nesting", label: "Nest", align: "right", sortable: true },
  { key: "nloc", label: "NLOC", align: "right", sortable: true },
  { key: "duplication_pct", label: "Dup %", align: "right", sortable: true },
  { key: "line_coverage_pct", label: "Cov", align: "right", sortable: true },
  { key: "tests", label: "Tests", align: "center", sortable: false },
];

export function HealthFileTable({
  files,
  sortField = "score",
  sortOrder = "asc",
  onSort,
  onSelect,
  selectedPath,
  emptyMessage,
}: HealthFileTableProps) {
  if (files.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-secondary)]">
        {emptyMessage ?? "No files match the current filters."}
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider sticky top-0">
          <tr>
            {COLS.map((c) => {
              const isActive = c.sortable && c.key === sortField;
              const alignCls =
                c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left";
              return (
                <th
                  key={c.key}
                  className={`px-3 py-2 font-medium ${alignCls} ${c.sortable ? "cursor-pointer select-none" : ""}`}
                  onClick={c.sortable && onSort ? () => onSort(c.key as FileSortField) : undefined}
                >
                  <span className="inline-flex items-center gap-1">
                    {c.label}
                    {isActive ? (
                      sortOrder === "asc" ? (
                        <ArrowUp className="h-3 w-3" />
                      ) : (
                        <ArrowDown className="h-3 w-3" />
                      )
                    ) : null}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {files.map((f) => {
            const isSelected = selectedPath === f.file_path;
            const rowCls = `border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)] ${
              isSelected ? "bg-[var(--color-accent-muted)]/30" : ""
            } ${onSelect ? "cursor-pointer" : ""}`;
            return (
              <tr
                key={f.file_path}
                className={rowCls}
                onClick={onSelect ? () => onSelect(f) : undefined}
              >
                <td className="px-3 py-2 text-[var(--color-text-primary)] font-mono text-xs truncate max-w-[440px]">
                  <span className="inline-flex items-center gap-1.5">
                    <FileText className="h-3 w-3 text-[var(--color-text-tertiary)]" />
                    <span className="truncate" title={f.file_path}>
                      {f.file_path}
                    </span>
                  </span>
                </td>
                <td className="px-3 py-2 text-right">
                  <span
                    className={`inline-block rounded px-2 py-0.5 text-xs font-semibold tabular-nums ${scoreBadgeClass(f.score)}`}
                  >
                    {f.score.toFixed(1)}
                  </span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">{f.max_ccn}</td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">{f.max_nesting}</td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">{f.nloc}</td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">
                  {f.duplication_pct == null ? "—" : `${f.duplication_pct.toFixed(0)}%`}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">
                  {f.line_coverage_pct == null ? "—" : `${f.line_coverage_pct.toFixed(0)}%`}
                </td>
                <td className="px-3 py-2 text-center text-xs">
                  {f.has_test_file ? (
                    <span className="text-emerald-500">✓</span>
                  ) : (
                    <span className="text-[var(--color-text-tertiary)]">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
