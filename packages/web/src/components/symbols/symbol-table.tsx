"use client";

import { useState, useEffect, useCallback } from "react";
import useSWR from "swr";
import { ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { SymbolDrawer } from "./symbol-drawer";
import { listSymbols } from "@/lib/api/symbols";
import { useDebounce } from "@/lib/hooks/use-debounce";
import { truncatePath } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";
import type { SymbolResponse } from "@/lib/api/types";

type SortCol = "name" | "kind" | "language" | "complexity_estimate" | "start_line";
type SortDir = "asc" | "desc";

const LIMIT = 50;

const KINDS = ["function", "class", "method", "interface", "variable", "module"];
const LANGUAGES = ["python", "typescript", "javascript", "go", "rust", "java", "cpp", "c"];

interface SymbolTableProps {
  repoId: string;
}

function SortIcon({ col, sortCol, sortDir }: { col: SortCol; sortCol: SortCol; sortDir: SortDir }) {
  if (col !== sortCol) return <ChevronsUpDown className="h-3 w-3 opacity-40" />;
  return sortDir === "asc" ? (
    <ChevronUp className="h-3 w-3" />
  ) : (
    <ChevronDown className="h-3 w-3" />
  );
}

export function SymbolTable({ repoId }: SymbolTableProps) {
  const [q, setQ] = useState("");
  const debouncedQ = useDebounce(q, 300);
  const [kind, setKind] = useState("all");
  const [language, setLanguage] = useState("all");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<SymbolResponse[]>([]);
  const [sortCol, setSortCol] = useState<SortCol>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [selected, setSelected] = useState<SymbolResponse | null>(null);

  const swrKey = `symbols:${repoId}:${debouncedQ}:${kind}:${language}:${offset}`;
  const { data, isLoading } = useSWR<SymbolResponse[]>(
    swrKey,
    () =>
      listSymbols({
        repo_id: repoId,
        q: debouncedQ || undefined,
        kind: kind !== "all" ? kind : undefined,
        language: language !== "all" ? language : undefined,
        limit: LIMIT,
        offset,
      }),
    { revalidateOnFocus: false },
  );

  useEffect(() => {
    if (!data) return;
    setItems((prev) => (offset === 0 ? data : [...prev, ...data]));
  }, [data, offset]);

  // Reset when filters change
  useEffect(() => {
    setOffset(0);
    setItems([]);
  }, [debouncedQ, kind, language]);

  const handleSort = useCallback(
    (col: SortCol) => {
      if (col === sortCol) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setSortCol(col);
        setSortDir("asc");
      }
    },
    [sortCol],
  );

  const sorted = [...items].sort((a, b) => {
    const va = a[sortCol] ?? "";
    const vb = b[sortCol] ?? "";
    const cmp = String(va).localeCompare(String(vb), undefined, { numeric: true });
    return sortDir === "asc" ? cmp : -cmp;
  });

  const hasMore = data?.length === LIMIT;

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <Input
          placeholder="Search symbols…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="max-w-xs"
        />
        <Select value={kind} onValueChange={setKind}>
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
        <Select value={language} onValueChange={setLanguage}>
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

      {/* Table */}
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
              <thead>
                <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
                  {(
                    [
                      { col: "name" as SortCol, label: "Name" },
                      { col: "kind" as SortCol, label: "Kind" },
                      { col: "language" as SortCol, label: "Language" },
                      { col: "start_line" as SortCol, label: "File" },
                      { col: "complexity_estimate" as SortCol, label: "Complexity" },
                    ] as Array<{ col: SortCol; label: string }>
                  ).map(({ col, label }) => (
                    <th
                      key={col}
                      className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider cursor-pointer select-none hover:text-[var(--color-text-primary)] transition-colors"
                      onClick={() => handleSort(col)}
                    >
                      <span className="inline-flex items-center gap-1">
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
                    className="border-b border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)] transition-colors last:border-0 cursor-pointer"
                    onClick={() => setSelected(sym)}
                  >
                    <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-primary)]" style={{ maxWidth: 0 }}>
                      <span className="truncate block" title={sym.qualified_name || sym.name}>{sym.name}</span>
                      {sym.parent_name && (
                        <span className="text-[var(--color-text-tertiary)]">.{sym.parent_name}</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge variant={sym.kind === "class" ? "accent" : "default"}>{sym.kind}</Badge>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-[var(--color-text-secondary)]">
                      {sym.language}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-tertiary)]" style={{ maxWidth: 0 }}>
                      <span className="block truncate" title={`${sym.file_path}:${sym.start_line}`}>
                        {truncatePath(sym.file_path)}:{sym.start_line}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-[var(--color-text-secondary)] tabular-nums">
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
                onClick={() => setOffset((o) => o + LIMIT)}
                disabled={isLoading}
              >
                {isLoading ? "Loading…" : "Load more"}
              </Button>
            </div>
          )}
        </>
      )}

      <SymbolDrawer symbol={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
