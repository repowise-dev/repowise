"use client";

import { type ReactNode } from "react";
import {
  BookOpen,
  Flame,
  GitBranch,
  Globe2,
  Lock,
  Rocket,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { Input } from "../ui/input";
import { Badge } from "../ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { Skeleton } from "../ui/skeleton";
import { EmptyState } from "../shared/empty-state";
import { ResultsFooter } from "../shared/results-footer";
import { RowActions } from "../shared/row-actions";
import { truncatePath } from "../lib/format";
import { cn } from "../lib/cn";
import type { CodeSymbol } from "@repowise-dev/types/symbols";

const KINDS = ["function", "class", "method", "interface", "variable", "module"];
const LANGUAGES = ["python", "typescript", "javascript", "go", "rust", "java", "cpp", "c"];
const VISIBILITIES = ["public", "protected", "private"];

export type SymbolSortKey = "importance" | "name" | "complexity" | "kind";

/**
 * The full filter set the server understands. Kept as a value object so
 * the wrapper owns a single piece of state and there's no chance of the
 * fetch key drifting from the rendered controls.
 */
export interface SymbolFilters {
  q: string;
  kind: string;
  language: string;
  visibility: string;
  inHotFiles: boolean;
  inEntryPoints: boolean;
  sort: SymbolSortKey;
}

export interface SymbolTableProps {
  items: CodeSymbol[];
  isLoading: boolean;
  isValidating: boolean;
  hasMore: boolean;
  /** Authoritative count from the server's paginated envelope. */
  total: number;
  filters: SymbolFilters;
  onFiltersChange: (next: SymbolFilters) => void;
  onLoadMore: () => void;
  onSelect: (sym: CodeSymbol) => void;
  repoId?: string;
  linkPrefix?: string;
  drawer?: ReactNode;
}

function ImportanceBar({ score }: { score: number | null | undefined }) {
  const pct = Math.min(100, Math.max(0, Math.round((score ?? 0) * 100)));
  const color =
    pct >= 70
      ? "bg-[var(--color-accent-primary)]"
      : pct >= 40
        ? "bg-yellow-500"
        : "bg-[var(--color-text-tertiary)]";
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-16 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] tabular-nums text-[var(--color-text-tertiary)]">{pct}</span>
    </div>
  );
}

/**
 * Compact signal chips that summarise the row's most important non-name
 * properties at a glance. Kept terse so the table stays scannable.
 */
function SignalChips({ sym }: { sym: CodeSymbol }) {
  const isPublic = sym.visibility === "public";
  const isPrivate = sym.visibility === "private";
  const complex = (sym.complexity_estimate ?? 0) >= 15;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span
        className={cn(
          "inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px] font-medium",
          isPublic
            ? "bg-emerald-500/15 text-emerald-400"
            : isPrivate
              ? "bg-slate-500/15 text-slate-400"
              : "bg-amber-500/15 text-amber-400",
        )}
        title={`Visibility: ${sym.visibility}`}
      >
        {isPublic ? <Globe2 className="h-2.5 w-2.5" /> : isPrivate ? <Lock className="h-2.5 w-2.5" /> : null}
        {sym.visibility}
      </span>
      {sym.is_entry_point && (
        <span
          className="inline-flex items-center gap-0.5 rounded bg-blue-500/15 px-1 py-0.5 text-[10px] font-medium text-blue-400"
          title="Lives in an entry-point file"
        >
          <Rocket className="h-2.5 w-2.5" />
          entry
        </span>
      )}
      {sym.file_is_hotspot && (
        <span
          className="inline-flex items-center gap-0.5 rounded bg-red-500/15 px-1 py-0.5 text-[10px] font-medium text-red-400"
          title="File is a churn hotspot"
        >
          <Flame className="h-2.5 w-2.5" />
          hot
        </span>
      )}
      {complex && (
        <span
          className="inline-flex items-center gap-0.5 rounded bg-yellow-500/15 px-1 py-0.5 text-[10px] font-medium text-yellow-400"
          title="High complexity"
        >
          <Sparkles className="h-2.5 w-2.5" />
          complex
        </span>
      )}
    </span>
  );
}

/**
 * Toggle pill — used for boolean filter facets ("in hot files",
 * "entry points only"). Lifted into a tiny helper so the toolbar reads
 * declaratively.
 */
function FacetToggle({
  active,
  onClick,
  label,
  icon,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  icon: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors",
        active
          ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/15 text-[var(--color-accent-primary)]"
          : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

export function SymbolTable({
  items,
  isLoading,
  isValidating,
  hasMore,
  total,
  filters,
  onFiltersChange,
  onLoadMore,
  onSelect,
  repoId,
  linkPrefix,
  drawer,
}: SymbolTableProps) {
  const prefix = linkPrefix ?? (repoId ? `/repos/${repoId}` : undefined);

  function patch(p: Partial<SymbolFilters>) {
    onFiltersChange({ ...filters, ...p });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search symbols…"
          value={filters.q}
          onChange={(e) => patch({ q: e.target.value })}
          className="max-w-xs"
        />
        <Select value={filters.kind} onValueChange={(v) => patch({ kind: v })}>
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
        <Select value={filters.language} onValueChange={(v) => patch({ language: v })}>
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
        <Select value={filters.visibility} onValueChange={(v) => patch({ visibility: v })}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="Visibility" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All visibilities</SelectItem>
            {VISIBILITIES.map((v) => (
              <SelectItem key={v} value={v}>
                {v}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <FacetToggle
          active={filters.inHotFiles}
          onClick={() => patch({ inHotFiles: !filters.inHotFiles })}
          label="In hot files"
          icon={<Flame className="h-3 w-3" />}
        />
        <FacetToggle
          active={filters.inEntryPoints}
          onClick={() => patch({ inEntryPoints: !filters.inEntryPoints })}
          label="Entry points"
          icon={<Rocket className="h-3 w-3" />}
        />
        <Select value={filters.sort} onValueChange={(v) => patch({ sort: v as SymbolSortKey })}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Sort" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="importance">Importance</SelectItem>
            <SelectItem value="name">Name (A→Z)</SelectItem>
            <SelectItem value="complexity">Complexity</SelectItem>
            <SelectItem value="kind">Kind</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {isLoading && items.length === 0 ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState title="No symbols found" description="Try adjusting your filters." />
      ) : (
        <div className="rounded-lg border border-[var(--color-border-default)] overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10">
                <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-32">
                    <span className="inline-flex items-center gap-1">
                      <TrendingUp className="h-3 w-3" />
                      Importance
                    </span>
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                    Name
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                    Signals
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden sm:table-cell">
                    Kind
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden sm:table-cell">
                    File
                  </th>
                  <th className="px-4 py-2.5 text-right text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden md:table-cell">
                    Complexity
                  </th>
                  {prefix && <th className="px-2 py-2.5 w-12" />}
                </tr>
              </thead>
              <tbody>
                {items.map((sym) => (
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
                      <ImportanceBar score={sym.importance_score} />
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-primary)] min-w-[200px] max-w-[420px]">
                      <span className="truncate block" title={sym.qualified_name || sym.name}>
                        {sym.name}
                      </span>
                      {sym.parent_name && (
                        <span
                          className="block truncate text-[var(--color-text-tertiary)]"
                          title={sym.parent_name}
                        >
                          .{sym.parent_name}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <SignalChips sym={sym} />
                    </td>
                    <td className="px-4 py-2.5 hidden sm:table-cell">
                      <Badge variant={sym.kind === "class" ? "accent" : "default"}>{sym.kind}</Badge>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-tertiary)] min-w-[200px] max-w-[420px] hidden sm:table-cell">
                      <span className="block truncate" title={`${sym.file_path}:${sym.start_line}`}>
                        {truncatePath(sym.file_path)}:{sym.start_line}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-[var(--color-text-secondary)] tabular-nums hidden md:table-cell text-right">
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
                    {prefix && (
                      <td className="px-2 py-2.5" onClick={(e) => e.stopPropagation()}>
                        <RowActions
                          actions={[
                            {
                              icon: GitBranch,
                              label: "Graph",
                              href: `${prefix}/graph?node=${encodeURIComponent(sym.file_path)}`,
                            },
                            {
                              icon: BookOpen,
                              label: "Docs",
                              href: `${prefix}/docs?file=${encodeURIComponent(sym.file_path)}`,
                            },
                          ]}
                        />
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ResultsFooter
            shown={items.length}
            total={total}
            hasMore={hasMore}
            loading={isValidating}
            onLoadMore={onLoadMore}
            noun="symbols"
          />
        </div>
      )}

      {drawer}
    </div>
  );
}
