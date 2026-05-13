"use client";

import { useRouter } from "next/navigation";
import { useMemo } from "react";
import useSWR from "swr";
import { SymbolGitPanel } from "@repowise-dev/ui/symbols/symbol-git-panel";
import { getCoChanges, getGitMetadata } from "@/lib/api/git";
import { listDeadCode } from "@/lib/api/dead-code";
import type { SymbolResponse } from "@/lib/api/types";

interface Props {
  symbol: SymbolResponse;
  repoId: string;
}

/**
 * Data-coupled wrapper around {@link SymbolGitPanel}. Fetches per-file
 * git metadata, co-changes, and any dead-code findings whose line range
 * overlaps the symbol — composed from existing endpoints so this panel
 * is purely additive (no ingestion changes required).
 */
export function SymbolGitPanelWrapper({ symbol, repoId }: Props) {
  const router = useRouter();
  const filePath = symbol.file_path;

  const { data: git, isLoading: gitLoading } = useSWR(
    `git-meta:${repoId}:${filePath}`,
    () =>
      getGitMetadata(repoId, filePath).catch((err) => {
        // 404 is expected for files outside the indexed history (vendored,
        // generated, etc.) — fall back to null so the panel hides itself.
        if (err?.status === 404) return null;
        throw err;
      }),
    { revalidateOnFocus: false },
  );

  const { data: coChangePayload } = useSWR(
    git ? `co-changes:${repoId}:${filePath}` : null,
    () => getCoChanges(repoId, filePath, 2),
    { revalidateOnFocus: false },
  );

  const { data: deadFindings } = useSWR(
    `dead-code:${repoId}`,
    () => listDeadCode(repoId, { limit: 500 }),
    { revalidateOnFocus: false },
  );

  const overlappingDead = useMemo(() => {
    if (!deadFindings) return [];
    return deadFindings.filter((f) => {
      if (f.file_path !== filePath) return false;
      // The dead-code finding doesn't carry line ranges in this shape, so
      // we conservatively match by file + symbol name when available.
      if (!f.symbol_name) return false;
      return (
        f.symbol_name === symbol.name ||
        f.symbol_name === symbol.qualified_name
      );
    });
  }, [deadFindings, filePath, symbol.name, symbol.qualified_name]);

  return (
    <SymbolGitPanel
      filePath={filePath}
      loading={gitLoading}
      git={
        git
          ? {
              primary_owner_name: git.primary_owner_name,
              primary_owner_commit_pct: git.primary_owner_commit_pct,
              recent_owner_name: git.recent_owner_name,
              recent_owner_commit_pct: git.recent_owner_commit_pct,
              bus_factor: git.bus_factor,
              contributor_count: git.contributor_count,
              commit_count_90d: git.commit_count_90d,
              is_hotspot: git.is_hotspot,
              churn_percentile: git.churn_percentile,
              top_authors: git.top_authors,
            }
          : null
      }
      coChanges={coChangePayload?.co_change_partners ?? []}
      deadCode={overlappingDead.map((f) => ({
        id: f.id,
        kind: f.kind,
        reason: f.reason,
        confidence: f.confidence,
        lines: f.lines,
        safe_to_delete: f.safe_to_delete,
      }))}
      onOpenBlastRadius={() =>
        router.push(`/repos/${repoId}/blast-radius?file=${encodeURIComponent(filePath)}`)
      }
    />
  );
}
