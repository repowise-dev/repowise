"use client";

import { useState, useCallback, useMemo, type ReactNode } from "react";
import { ChevronUp, ChevronDown, ChevronsUpDown, TrendingUp } from "lucide-react";
import { Input } from "../ui/input";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { Skeleton } from "../ui/skeleton";
import { EmptyState } from "../shared/empty-state";
import { truncatePath } from "../lib/format";
import { cn } from "../lib/cn";
import type { CodeSymbol } from "@repowise-dev/types/symbols";

export type SortCol = "importance" | "name" | "kind" | "language" | "complexity_estimate" | "start_line";
export type SortDir = "asc" | "desc";

const KINDS = ["function", "class", "method", "interface", "variable", "module"];
const LANGUAGES = ["python", "typescript", "javascript", "go", "rust", "java", "cpp", "c"];

export interface SymbolTableProps {
  /** Loaded symbols (already paginated and filtered server-side). */
  items: CodeSymbol[];
  /** Map of symbol.id → normalized importance score (0–1). */
  importanceScores: Map<string, number>;
  isLoading: boolean;
  isValidating: boolean;
  hasMore: boolean;
  /** Server-driven filters — these affect the fetch key, so they live in the wrapper. */
  q: string;
  onQChange: (v: string) => void;
  kind: string;
  onKindChange: (v: string) => void;
  language: string;
  onLanguageChange: (v: string) => void;
  onLoadMore: () => void;
  onSelect: (sym: CodeSymbol) => void;
  /** Drawer slot for the selected row. Wrapper passes a `<SymbolDrawer>` mounted with its own data. */
  drawer?: ReactNode;
}

function SortIcon({ col, sortCol, sortDir }: { col: SortCol; sortCol: SortCol; sortDir: SortDir }) {
  if (col !== sortCol) return <ChevronsUpDown className="h-3 w-3 opacity-40" />;
  return sortDir === "asc" ? (
    <ChevronUp className="h-3 w-3" />
  ) : (
    <ChevronDown className="h-3 w-3" />
  );
}

function ImportanceBar({ score }: { score: number }) {
  const pct = Math.min(100, Math.round(score * 100));
  const color = pct >= 70 ? "bg-[var(--color-accent-primary)]" : pct >= 40 ? "bg-yellow-500" : "bg-[var(--color-text-tertiary)]";
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-16 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] tabular-nums text-[var(--color-text-tertiary)]">{pct}</span>
    </div>
  );
}

export function SymbolTable({
  items,
  importanceScores,
  isLoading,
  isValidating,
  hasMore,
  q,
  onQChange,
  kind,
  onKindChange,
  language,
  onLanguageChange,
  onLoadMore,
  onSelect,
  drawer,
}: SymbolTableProps) {
  const [sortCol, setSortCol] = useState<SortCol>("importance");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const handleSort = useCallback(
    (col: SortCol) => {
      if (col === sortCol) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setSortCol(col);
        setSortDir(col === "importance" ? "desc" : "asc");
      }
    },
    [sortCol],
  );

  const sorted = useMemo(() => {
    return [...items].sort((a, b) => {
      if (sortCol === "importance") {
        const ia = importanceScores.get(a.id) ?? 0;
        const ib = importanceScores.get(b.id) ?? 0;
        return sortDir === "asc" ? ia - ib : ib - ia;
      }
      const va = (a as unknown as Record<string, unknown>)[sortCol] ?? "";
      const vb = (b as unknown as Record<string, unknown>)[sortCol] ?? "";
      const cmp = String(va).localeCompare(String(vb), undefined, { numeric: true });
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [items, sortCol, sortDir, importanceScores]);

  const columns: Array<{ col: SortCol; label: string; hideOnMobile?: boolean }> = [
    { col: "importance", label: "Importance" },
    { col: "name", label: "Name" },
    { col: "kind", label: "Kind" },
    { col: "language", label: "Language", hideOnMobile: true },
    { col: "start_line", label: "File", hideOnMobile: true },
    { col: "complexity_estimate", label: "Complexity", hideOnMobile: true },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Input
          placeholder="Search symbols…"
          value={q}
          onChange={(e) => onQChange(e.target.value)}
          className="max-w-xs"
        />
        <Select value={kind} onValueChange={onKindChange}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="Kind" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All kinds</SelectItem>
            {KINDS.map((k) => (
              <SelectItem key={k} value={k}>
                {k}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={language} onValueChange={onLanguageChange}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="Language" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All languages</SelectItem>
            {LANGUAGES.map((l) => (
              <SelectItem key={l} value={l}>
                {l}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {items.length > 0 && (
          <span className="self-center text-xs text-[var(--color-text-tertiary)]">
            {items.length} symbols
          </span>
        )}
      </div>

      {isLoading && items.length === 0 ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <EmptyState title="No symbols found" description="Try adjusting your filters." />
      ) : (
        <>
          <div className="rounded-lg border border-[var(--color-border-default)] overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10">
                <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
                  {columns.map(({ col, label, hideOnMobile }) => (
                    <th
                      key={col}
                      scope="col"
                      aria-sort={
                        sortCol === col
                          ? sortDir === "asc"
                            ? "ascending"
                            : "descending"
                          : "none"
                      }
                      className={cn(
                        "px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider cursor-pointer select-none hover:text-[var(--color-text-primary)] transition-colors bg-[var(--color-bg-elevated)]",
                        hideOnMobile && "hidden sm:table-cell",
                      )}
                      onClick={() => handleSort(col)}
                    >
                      <span className="inline-flex items-center gap-1">
                        {col === "importance" && <TrendingUp className="h-3 w-3" />}
                        {label}
                        <SortIcon col={col} sortCol={sortCol} sortDir={sortDir} />
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((sym) => (
                  <tr
                    key={sym.id}
                    className="border-b border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)] transition-colors last:border-0 cursor-pointer focus:outline-none focus:bg-[var(--color-bg-elevated)]"
                    tabIndex={0}
                    role="button"
                    aria-label={`View ${sym.qualified_name || sym.name}`}
                    onClick={() => onSelect(sym)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect(sym);
                      }
                    }}
                  >
                    <td className="px-4 py-2.5">
                      <ImportanceBar score={importanceScores.get(sym.id) ?? 0} />
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-primary)] min-w-[200px] max-w-[420px]">
                      <span className="truncate block" title={sym.qualified_name || sym.name}>{sym.name}</span>
                      {sym.parent_name && (
                        <span className="block truncate text-[var(--color-text-tertiary)]" title={sym.parent_name}>.{sym.parent_name}</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge variant={sym.kind === "class" ? "accent" : "default"}>{sym.kind}</Badge>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-[var(--color-text-secondary)] hidden sm:table-cell">
                      {sym.language}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-tertiary)] min-w-[200px] max-w-[420px] hidden sm:table-cell">
                      <span className="block truncate" title={`${sym.file_path}:${sym.start_line}`}>
                        {truncatePath(sym.file_path)}:{sym.start_line}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-[var(--color-text-secondary)] tabular-nums hidden sm:table-cell text-right">
                      <span
                        className={cn(
                          sym.complexity_estimate > 15
                            ? "text-red-500"
                            : sym.complexity_estimate > 8
                              ? "text-yellow-500"
                              : "",
                        )}
                      >
                        {sym.complexity_estimate}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {hasMore && (
            <div className="flex justify-center">
              <Button
                variant="outline"
                size="sm"
                onClick={onLoadMore}
                disabled={isValidating}
              >
                {isValidating ? "Loading…" : "Load more"}
              </Button>
            </div>
          )}
        </>
      )}

      {drawer}
    </div>
  );
}
