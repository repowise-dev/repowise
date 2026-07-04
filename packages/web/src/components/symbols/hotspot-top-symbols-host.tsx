"use client";

import useSWR from "swr";
import { TopSymbolsRow } from "@repowise-dev/ui/symbols/top-symbols-row";
import { listSymbolsPage } from "@/lib/api/symbols";
import type { SymbolResponse } from "@/lib/api/types";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";

interface Props {
  repoId: string;
  filePath: string;
  /** Opens the universal SymbolDrawer from the host page. */
  onSelectSymbol: (symbol: SymbolResponse) => void;
}

/**
 * SWR host for the hotspot-row → top-symbols inline expand. Pulls the top 10
 * importance-ranked symbols in the given file, then defers rendering to the
 * shared TopSymbolsRow.
 */
export function HotspotTopSymbolsHost({ repoId, filePath, onSelectSymbol }: Props) {
  const { data, error, isLoading } = useSWR(
    `top-symbols-in-file:${repoId}:${filePath}`,
    () =>
      listSymbolsPage({
        repo_id: repoId,
        file_path: filePath,
        sort: "importance",
        limit: 10,
      }),
    { revalidateOnFocus: false },
  );

  const seeAllHref = `/repos/${repoId}/architecture?view=symbols&q=${encodeURIComponent(filePath)}`;

  return (
    <TopSymbolsRow
      symbols={data?.items}
      loading={isLoading}
      error={error ? toFriendlyMessage(error) : undefined}
      onSelect={onSelectSymbol}
      seeAllHref={seeAllHref}
    />
  );
}
