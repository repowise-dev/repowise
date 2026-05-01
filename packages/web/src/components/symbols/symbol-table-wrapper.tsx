"use client";

import { useState, useMemo } from "react";
import useSWR from "swr";
import useSWRInfinite from "swr/infinite";
import { SymbolTable } from "@repowise-dev/ui/symbols/symbol-table";
import { SymbolDrawerWrapper } from "./symbol-drawer-wrapper";
import { listSymbols } from "@/lib/api/symbols";
import { getGraph } from "@/lib/api/graph";
import { useDebounce } from "@/lib/hooks/use-debounce";
import type { SymbolResponse, GraphExportResponse } from "@/lib/api/types";

const LIMIT = 50;

interface Props {
  repoId: string;
}

export function SymbolTableWrapper({ repoId }: Props) {
  const [q, setQ] = useState("");
  const debouncedQ = useDebounce(q, 300);
  const [kind, setKind] = useState("all");
  const [language, setLanguage] = useState("all");
  const [selected, setSelected] = useState<SymbolResponse | null>(null);

  const { data: graphData } = useSWR<GraphExportResponse>(
    `graph:${repoId}`,
    () => getGraph(repoId),
    { revalidateOnFocus: false, revalidateOnReconnect: false },
  );

  const pagerankMap = useMemo(() => {
    if (!graphData) return new Map<string, number>();
    const m = new Map<string, number>();
    for (const n of graphData.nodes) m.set(n.node_id, n.pagerank);
    return m;
  }, [graphData]);

  const { data, size, setSize, isLoading, isValidating } = useSWRInfinite<SymbolResponse[]>(
    (pageIndex, previousPageData) => {
      if (previousPageData && previousPageData.length < LIMIT) return null;
      return `symbols:${repoId}:${debouncedQ}:${kind}:${language}:${pageIndex}`;
    },
    (key) => {
      const pageIndex = parseInt(key.split(":").pop()!, 10);
      return listSymbols({
        repo_id: repoId,
        q: debouncedQ || undefined,
        kind: kind !== "all" ? kind : undefined,
        language: language !== "all" ? language : undefined,
        limit: LIMIT,
        offset: pageIndex * LIMIT,
      });
    },
    { revalidateOnFocus: false, revalidateFirstPage: false },
  );

  const items = useMemo(() => (data ? data.flat() : []), [data]);

  const importanceScores = useMemo(() => {
    const scores = new Map<string, number>();
    for (const sym of items) {
      const fileRank = pagerankMap.get(sym.file_path) ?? 0;
      const complexity = Math.max(1, sym.complexity_estimate);
      scores.set(sym.id, fileRank * (1 + Math.log(complexity)));
    }
    const max = Math.max(...scores.values(), 0.0001);
    for (const [k, v] of scores) scores.set(k, v / max);
    return scores;
  }, [items, pagerankMap]);

  const lastPage = data ? data[data.length - 1] : undefined;
  const hasMore = lastPage?.length === LIMIT;

  return (
    <SymbolTable
      items={items}
      importanceScores={importanceScores}
      isLoading={isLoading}
      isValidating={isValidating}
      hasMore={hasMore}
      q={q}
      onQChange={setQ}
      kind={kind}
      onKindChange={setKind}
      language={language}
      onLanguageChange={setLanguage}
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
  );
}
