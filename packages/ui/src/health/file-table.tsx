"use client";

import { useMemo } from "react";
import { FileText } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import { biomarkerLabel } from "./biomarker-glossary";
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
  /** Dominant-cause lead: the worst finding's biomarker + reason (P3). */
  primary_biomarker?: string | null;
  primary_reason?: string | null;
  /** Summed pre-floor `health_impact` — the depth behind a floored score (P1). */
  total_deduction?: number | null;
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
  // When sorting by score, two floored files both read 1.0; break that tie by
  // deduction magnitude so the deeper problem sorts first (server sorts by
  // score only, so this is a within-page refinement — P1).
  const rows = useMemo(() => {
    if (sortField !== "score") return files;
    const dir = sortOrder === "desc" ? -1 : 1;
    return [...files].sort((a, b) => {
      if (a.score !== b.score) return (a.score - b.score) * dir;
      return (b.total_deduction ?? 0) - (a.total_deduction ?? 0);
    });
  }, [files, sortField, sortOrder]);

  const columns: ResponsiveColumn<HealthFileRow>[] = [
    {
      key: "file_path",
      header: "File",
      priority: 1,
      sortable: true,
      cellClassName: "text-[var(--color-text-primary)] max-w-[440px]",
      render: (f) => (
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="flex items-center gap-1.5 min-w-0 font-mono text-xs">
            <FileText className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
            <span className="truncate" title={f.file_path}>
              {f.file_path}
            </span>
          </span>
          {f.primary_biomarker ? (
            <span
              className="truncate pl-[18px] text-[11px] text-[var(--color-text-tertiary)]"
              title={f.primary_reason ?? undefined}
            >
              <span className="font-medium text-[var(--color-text-secondary)]">
                {biomarkerLabel(f.primary_biomarker)}
              </span>
              {f.primary_reason ? ` — ${f.primary_reason}` : ""}
            </span>
          ) : null}
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
        <span className="inline-flex flex-col items-end gap-0.5">
          <span
            className={`inline-block rounded px-2 py-0.5 text-xs font-semibold tabular-nums ${scoreBadgeClass(f.score)}`}
          >
            {f.score.toFixed(1)}
          </span>
          {/* Depth behind a floored score: two files that both print 1.0 are
              distinguishable by their summed deductions (P1 false-flag fix). */}
          {f.score <= 1 && f.total_deduction != null ? (
            <span
              className="text-[10px] tabular-nums text-[var(--color-error)]"
              title="Total deductions behind this floored score"
            >
              −{f.total_deduction.toFixed(1)}
            </span>
          ) : null}
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
      rows={rows}
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
