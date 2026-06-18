"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { SymbolDrawer } from "@repowise-dev/ui/symbols/symbol-drawer";
import {
  fileEntityPath,
  symbolEntityPath,
} from "@repowise-dev/ui/shared/entity";
import { useGraphMetrics, useCallersCallees } from "@/lib/hooks/use-graph";
import { getCoChanges, getGitMetadata } from "@/lib/api/git";
import { listDeadCode } from "@/lib/api/dead-code";
import type { SymbolResponse } from "@/lib/api/types";
import type { SymbolDetailData } from "@repowise-dev/types/symbols";

interface Props {
  symbol: SymbolResponse | null;
  repoId: string;
  onClose: () => void;
}

/**
 * Data-coupled adapter for the modal symbol surface. Fetches the graph
 * metrics, callers/callees, git metadata, co-changes, and dead-code feeds and
 * NORMALIZES them — together with the row's `SymbolResponse` — into the single
 * `SymbolDetailData` shape that `SymbolDetailBody` renders. This is the web
 * adapter referenced by the symbol-surface unification: the ui body stays pure.
 */
export function SymbolDrawerWrapper({ symbol, repoId, onClose }: Props) {
  const router = useRouter();
  const nodeId = symbol ? `${symbol.file_path}::${symbol.name}` : null;
  const filePath = symbol?.file_path ?? null;

  const { metrics } = useGraphMetrics(repoId, nodeId);
  const { data: callData, isLoading: callsLoading } = useCallersCallees(repoId, nodeId);

  const { data: git } = useSWR(
    filePath ? `git-meta:${repoId}:${filePath}` : null,
    () =>
      getGitMetadata(repoId, filePath!).catch((err) => {
        if (err?.status === 404) return null;
        throw err;
      }),
    { revalidateOnFocus: false },
  );

  const { data: coChangePayload } = useSWR(
    git && filePath ? `co-changes:${repoId}:${filePath}` : null,
    () => getCoChanges(repoId, filePath!, 2),
    { revalidateOnFocus: false },
  );

  const { data: deadFindings } = useSWR(
    symbol ? `dead-code:${repoId}` : null,
    () => listDeadCode(repoId, { limit: 500 }),
    { revalidateOnFocus: false },
  );

  const overlappingDead = useMemo(() => {
    if (!deadFindings || !symbol) return [];
    return deadFindings.filter(
      (f) =>
        f.file_path === symbol.file_path &&
        f.symbol_name != null &&
        (f.symbol_name === symbol.name || f.symbol_name === symbol.qualified_name),
    );
  }, [deadFindings, symbol]);

  const data: SymbolDetailData | null = useMemo(() => {
    if (!symbol) return null;
    return {
      identity: {
        name: symbol.name,
        qualified_name: symbol.qualified_name,
        kind: symbol.kind,
        visibility: symbol.visibility,
        language: symbol.language,
        is_async: symbol.is_async,
        file_path: symbol.file_path,
        start_line: symbol.start_line,
        parent_name: symbol.parent_name,
        file_is_hotspot: symbol.file_is_hotspot ?? null,
      },
      signature: symbol.signature,
      docstring: symbol.docstring,
      importance_score: symbol.importance_score ?? null,
      complexity_estimate: symbol.complexity_estimate,
      graph: {
        in_degree: metrics?.in_degree ?? callData?.caller_count ?? 0,
        out_degree: metrics?.out_degree ?? callData?.callee_count ?? 0,
        callers: (callData?.callers ?? []).map((c) => ({
          symbol_id: c.symbol_id,
          name: c.name,
          file: c.file,
          edge_type: c.edge_type,
          confidence: c.confidence,
        })),
        callees: (callData?.callees ?? []).map((c) => ({
          symbol_id: c.symbol_id,
          name: c.name,
          file: c.file,
          edge_type: c.edge_type,
          confidence: c.confidence,
        })),
        pagerank_percentile: metrics?.pagerank_percentile ?? null,
        betweenness_percentile: metrics?.betweenness_percentile ?? null,
        community_label: metrics?.community_label ?? null,
        entry_point_score: metrics?.entry_point_score ?? null,
      },
      git: git
        ? {
            primary_owner_name: git.primary_owner_name,
            primary_owner_commit_pct: git.primary_owner_commit_pct,
            recent_owner_name: git.recent_owner_name,
            bus_factor: git.bus_factor,
            contributor_count: git.contributor_count,
            commit_count_90d: git.commit_count_90d,
            is_hotspot: git.is_hotspot,
            churn_percentile: git.churn_percentile,
          }
        : null,
      co_changes: (coChangePayload?.co_change_partners ?? []).map((p) => ({
        file_path: p.file_path,
        co_change_count: p.co_change_count,
      })),
      dead_code: overlappingDead.map((f) => ({
        id: f.id,
        kind: f.kind,
        reason: f.reason,
        lines: f.lines,
        safe_to_delete: f.safe_to_delete,
      })),
      file_context: { language: symbol.language },
    };
  }, [symbol, metrics, callData, git, coChangePayload, overlappingDead]);

  return (
    <SymbolDrawer
      data={data}
      onClose={onClose}
      metricsLoading={callsLoading}
      symbolHref={(symId) => symbolEntityPath(`/repos/${repoId}`, symId)}
      fileHref={(p) => fileEntityPath(`/repos/${repoId}`, p)}
      {...(filePath
        ? {
            onOpenBlastRadius: () =>
              router.push(
                `/repos/${repoId}/code-health?tab=impact&file=${encodeURIComponent(filePath)}`,
              ),
          }
        : {})}
    />
  );
}
