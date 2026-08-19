"use client";

import { useRef, useState } from "react";
import { ArrowDown, ArrowUp, FlaskConical, LogIn } from "lucide-react";
import type { FileRow } from "@repowise-dev/types/files";
import { cn } from "../lib/cn";
import { formatLOC, truncatePath } from "../lib/format";
import { healthInk } from "../health/tokens";

export type SortKey =
  | "dependents"
  | "health"
  | "churn"
  | "loc"
  | "coverage"
  | "name";

interface FilesTableProps {
  /** Already filtered + sorted by the parent. */
  files: FileRow[];
  fileHref: (path: string) => string;
  sortKey: SortKey;
  sortDir: "asc" | "desc";
  onSort: (key: SortKey) => void;
}

const ROW_HEIGHT = 44;
const OVERSCAN = 8;

/**
 * The health figure's colour, from the same function the treemap tiles use.
 *
 * This banded at 7 while `healthInk` bands at 8, so the 7.x files — and there
 * are a lot of them — read green in this column, green in the map's tooltip and
 * amber on the tile the tooltip was attached to. Three vocabularies for one
 * number on one page. Match the mark you sit next to; every other mark on this
 * page is on the canonical bands.
 */
function scoreInk(score: number | null): string {
  return score == null ? "var(--color-text-tertiary)" : healthInk(score);
}

function SortHeader({
  label,
  col,
  sortKey,
  sortDir,
  onSort,
  className,
  title,
}: {
  label: string;
  col: SortKey;
  sortKey: SortKey;
  sortDir: "asc" | "desc";
  onSort: (key: SortKey) => void;
  className?: string;
  /** What the column measures, for a header whose name is not self-evident. */
  title?: string;
}) {
  const active = sortKey === col;
  return (
    <button
      onClick={() => onSort(col)}
      title={title}
      className={cn(
        "flex items-center gap-1 text-left transition-colors hover:text-[var(--color-text-primary)]",
        active ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-tertiary)]",
        className,
      )}
    >
      {label}
      {active &&
        (sortDir === "asc" ? (
          <ArrowUp className="h-3 w-3" />
        ) : (
          <ArrowDown className="h-3 w-3" />
        ))}
    </button>
  );
}

// Column grid — kept in sync between header and rows. Trailing columns collapse
// on narrow widths (the `hidden sm:/md:` utilities) so the table stays readable
// on mobile without horizontal scroll.
// Column grid by breakpoint. Mobile keeps the three headline columns (File,
// Importance, Health); LOC joins at sm; Churn + Coverage at md. Counts match
// the cells' `hidden …:flex` visibility so the grid never mis-aligns or
// overflows — the first column is always `minmax(0,1fr)` so paths truncate.
const GRID =
  "grid grid-cols-[minmax(0,1fr)_auto_56px] sm:grid-cols-[minmax(0,1fr)_92px_64px_64px] md:grid-cols-[minmax(0,1fr)_100px_84px_72px_64px_72px] items-center gap-2 px-3 sm:px-4";

export function FilesTable({ files, fileHref, sortKey, sortDir, onSort }: FilesTableProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(600);

  const total = files.length;
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const end = Math.min(total, Math.ceil((scrollTop + viewportH) / ROW_HEIGHT) + OVERSCAN);
  const visible = files.slice(start, end);

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border-default)]">
      {/* Header — floating uppercase labels under a single rule, no fill. */}
      <div
        className={cn(
          GRID,
          "h-10 border-b border-[var(--color-border-default)] text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]",
        )}
      >
        <SortHeader label="File" col="name" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
        <SortHeader
          label="Dependents"
          col="dependents"
          title="PageRank percentile over the import graph: how much of the codebase reaches this file, directly or through others."
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={onSort}
          className="justify-end"
        />
        <SortHeader
          label="Health"
          col="health"
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={onSort}
          className="justify-end"
        />
        <SortHeader
          label="Churn"
          col="churn"
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={onSort}
          className="hidden justify-end md:flex"
        />
        <SortHeader
          label="LOC"
          col="loc"
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={onSort}
          className="hidden justify-end sm:flex"
        />
        <SortHeader
          label="Cov."
          col="coverage"
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={onSort}
          className="hidden justify-end md:flex"
        />
      </div>

      {/* Virtualized body */}
      <div
        ref={scrollRef}
        onScroll={(e) => {
          setScrollTop(e.currentTarget.scrollTop);
          setViewportH(e.currentTarget.clientHeight);
        }}
        className="relative max-h-[62vh] overflow-auto"
      >
        {total === 0 ? (
          <div className="flex h-32 items-center justify-center text-sm text-[var(--color-text-tertiary)]">
            No files match these filters.
          </div>
        ) : (
          <div style={{ height: total * ROW_HEIGHT }} className="relative">
            <div style={{ transform: `translateY(${start * ROW_HEIGHT}px)` }}>
              {visible.map((f) => {
                const name = f.file_path.split("/").pop() ?? f.file_path;
                const dir = f.file_path.slice(0, f.file_path.length - name.length);
                return (
                  <a
                    key={f.file_path}
                    href={fileHref(f.file_path)}
                    style={{ height: ROW_HEIGHT }}
                    className={cn(
                      GRID,
                      "group border-b border-[var(--color-table-divider)] text-sm transition-colors hover:bg-[var(--color-bg-elevated)]",
                    )}
                  >
                    {/* File path */}
                    <span className="flex min-w-0 items-center gap-1.5">
                      {f.is_entry_point && (
                        <LogIn className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)]" />
                      )}
                      {f.is_test && (
                        <FlaskConical className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
                      )}
                      <span className="min-w-0 truncate font-mono text-[13px]">
                        <span className="text-[var(--color-text-tertiary)]">
                          {truncatePath(dir, 40)}
                        </span>
                        <span className="text-[var(--color-text-primary)] underline-offset-2 group-hover:underline">
                          {name}
                        </span>
                      </span>
                    </span>

                    {/* Dependents. One decimal, not `Math.round`: the server
                        rounds the percentile to 0.1 and rounding again to a
                        whole number printed "100" on every row above the
                        99.5th, so a repo of 5,000 files led with ~25 rows
                        showing one identical figure for 25 different values —
                        a sort you could not see the basis of. */}
                    <span className="flex items-center justify-end gap-1.5 tabular-nums text-[var(--color-text-secondary)]">
                      <span
                        className="hidden h-1.5 rounded-full bg-[var(--color-accent-primary)] sm:inline-block"
                        style={{ width: `${Math.max(2, (f.pagerank_pct / 100) * 28)}px` }}
                      />
                      {f.pagerank_pct.toFixed(1)}
                    </span>

                    {/* Health */}
                    <span
                      className="flex justify-end tabular-nums"
                      style={{ color: scoreInk(f.defect_score) }}
                    >
                      {f.defect_score != null ? f.defect_score.toFixed(1) : "—"}
                    </span>

                    {/* Churn */}
                    <span className="hidden justify-end tabular-nums text-[var(--color-text-secondary)] md:flex">
                      {f.churn_pct != null ? `${Math.round(f.churn_pct)}` : "—"}
                    </span>

                    {/* LOC */}
                    <span className="hidden justify-end tabular-nums text-[var(--color-text-secondary)] sm:flex">
                      {f.loc != null ? formatLOC(f.loc) : "—"}
                    </span>

                    {/* Coverage */}
                    <span className="hidden justify-end tabular-nums text-[var(--color-text-secondary)] md:flex">
                      {f.coverage_pct != null ? `${Math.round(f.coverage_pct)}%` : "—"}
                    </span>
                  </a>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
