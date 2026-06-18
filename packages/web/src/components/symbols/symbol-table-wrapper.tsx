"use client";

import { useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { X } from "lucide-react";
import useSWRInfinite from "swr/infinite";
import { SymbolTable, type SymbolFilters } from "@repowise-dev/ui/symbols/symbol-table";
import { SymbolDrawerWrapper } from "./symbol-drawer-wrapper";
import { listSymbolsPage, type SymbolSortKey } from "@/lib/api/symbols";
import { useDebounce } from "@/lib/hooks/use-debounce";
import type { Paginated, SymbolResponse } from "@/lib/api/types";

const LIMIT = 50;

interface Props {
  repoId: string;
}

export function SymbolTableWrapper({ repoId }: Props) {
  // `?file=` deep link (graph inspection panel "Symbols" action) scopes the
  // table to one source file until dismissed.
  const searchParams = useSearchParams();
  const [fileFilter, setFileFilter] = useState<string | null>(
    () => searchParams.get("file"),
  );
  const [filters, setFilters] = useState<SymbolFilters>({
    q: "",
    kind: "all",
    language: "all",
    visibility: "all",
    inHotFiles: false,
    inEntryPoints: false,
    sort: "importance",
  });
  const debouncedQ = useDebounce(filters.q, 300);
  const [selected, setSelected] = useState<SymbolResponse | null>(null);

  // The fetch key changes whenever any filter does — SWR will fall back to
  // page 0 cleanly without us having to reset infinite state by hand.
  const keyPayload = useMemo(
    () => ({ ...filters, q: debouncedQ, file: fileFilter }),
    [filters, debouncedQ, fileFilter],
  );

  const { data, size, setSize, isLoading, isValidating } = useSWRInfinite<
    Paginated<SymbolResponse>
  >(
    (pageIndex, previousPageData) => {
      if (previousPageData && !previousPageData.has_more) return null;
      return `symbols:${repoId}:${JSON.stringify(keyPayload)}:${pageIndex}`;
    },
    (key) => {
      const pageIndex = parseInt(key.split(":").pop()!, 10);
      return listSymbolsPage({
        repo_id: repoId,
        q: keyPayload.q || undefined,
        kind: keyPayload.kind !== "all" ? keyPayload.kind : undefined,
        language: keyPayload.language !== "all" ? keyPayload.language : undefined,
        visibility:
          keyPayload.visibility !== "all" ? keyPayload.visibility : undefined,
        file_path: keyPayload.file || undefined,
        in_hot_files: keyPayload.inHotFiles || undefined,
        in_entry_points: keyPayload.inEntryPoints || undefined,
        sort: keyPayload.sort,
        limit: LIMIT,
        offset: pageIndex * LIMIT,
      });
    },
    { revalidateOnFocus: false, revalidateFirstPage: false },
  );

  const items = useMemo(
    () => (data ? data.flatMap((p) => p.items) : []),
    [data],
  );
  const total = data && data.length > 0 ? data[0].total : 0;
  const hasMore = data ? data[data.length - 1].has_more : false;

  return (
    <div className="space-y-6">
      {fileFilter && (
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-accent-primary)]/40 bg-[var(--color-accent-muted)] px-2.5 py-1 text-xs font-mono text-[var(--color-accent-primary)]">
            {fileFilter}
            <button
              type="button"
              onClick={() => setFileFilter(null)}
              aria-label="Clear file filter"
              className="hover:opacity-70"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
          <span className="text-xs text-[var(--color-text-tertiary)]">
            showing symbols in this file only
          </span>
        </div>
      )}
      <SymbolTable
        items={items}
        repoId={repoId}
        isLoading={isLoading}
        isValidating={isValidating}
        hasMore={hasMore}
        total={total}
        filters={filters}
        onFiltersChange={setFilters}
        onLoadMore={() => setSize(size + 1)}
        onSelect={setSelected}
        drawer={
          <SymbolDrawerWrapper
            symbol={selected}
            repoId={repoId}
            onClose={() => setSelected(null)}
          />
        }
      />
    </div>
  );
}
