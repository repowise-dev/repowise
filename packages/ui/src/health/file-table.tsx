"use client";

import { FileText } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
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

function num(value: number): string {
  return String(value);
}

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toFixed(0)}%`;
}

export function HealthFileTable({
  files,
  sortField = "score",
  sortOrder = "asc",
  onSort,
  onSelect,
  selectedPath,
  emptyMessage,
}: HealthFileTableProps) {
  const columns: ResponsiveColumn<HealthFileRow>[] = [
    {
      key: "file_path",
      header: "File",
      priority: 1,
      sortable: true,
      cellClassName: "text-[var(--color-text-primary)] font-mono text-xs max-w-[440px]",
      render: (f) => (
        <span className="flex items-center gap-1.5 min-w-0">
          <FileText className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
          <span className="truncate" title={f.file_path}>
            {f.file_path}
          </span>
        </span>
      ),
    },
    {
      key: "score",
      header: "Score",
      align: "right",
      priority: 1,
      sortable: true,
      render: (f) => (
        <span
          className={`inline-block rounded px-2 py-0.5 text-xs font-semibold tabular-nums ${scoreBadgeClass(f.score)}`}
        >
          {f.score.toFixed(1)}
        </span>
      ),
    },
    {
      key: "max_ccn",
      header: "CCN",
      align: "right",
      priority: 2,
      sortable: true,
      cellClassName: "tabular-nums text-[var(--color-text-secondary)]",
      render: (f) => num(f.max_ccn),
    },
    {
      key: "max_nesting",
      header: "Nest",
      align: "right",
      priority: 3,
      sortable: true,
      cellClassName: "tabular-nums text-[var(--color-text-secondary)]",
      render: (f) => num(f.max_nesting),
    },
    {
      key: "nloc",
      header: "NLOC",
      align: "right",
      priority: 3,
      sortable: true,
      cellClassName: "tabular-nums text-[var(--color-text-secondary)]",
      render: (f) => num(f.nloc),
    },
    {
      key: "duplication_pct",
      header: "Dup %",
      mobileLabel: "Dup",
      align: "right",
      priority: 2,
      sortable: true,
      cellClassName: "tabular-nums text-[var(--color-text-secondary)]",
      render: (f) => pct(f.duplication_pct),
    },
    {
      key: "line_coverage_pct",
      header: "Cov",
      align: "right",
      priority: 2,
      sortable: true,
      cellClassName: "tabular-nums text-[var(--color-text-secondary)]",
      render: (f) => pct(f.line_coverage_pct),
    },
    {
      key: "tests",
      header: "Tests",
      align: "center",
      priority: 3,
      cellClassName: "text-xs",
      render: (f) =>
        f.has_test_file ? (
          <span className="text-[var(--color-success)]">✓</span>
        ) : (
          <span className="text-[var(--color-text-tertiary)]">—</span>
        ),
    },
  ];

  return (
    <ResponsiveTable
      columns={columns}
      rows={files}
      rowKey={(f) => f.file_path}
      onRowClick={onSelect}
      selectedKey={selectedPath ?? null}
      sortField={sortField}
      sortOrder={sortOrder}
      onSort={onSort ? (key) => onSort(key as FileSortField) : undefined}
      empty={
        <EmptyState
          title="No files match"
          description={emptyMessage ?? "No files match the current filters."}
        />
      }
    />
  );
}
