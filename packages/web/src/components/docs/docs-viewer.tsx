"use client";

import { useCallback } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  Activity,
  Network,
  GitBranch,
  ArrowRight,
  Flame,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { getGitMetadata } from "@/lib/api/git";
import { DocsReader } from "@repowise-dev/ui/docs/docs-reader";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { VersionHistoryWrapper } from "@/components/wiki/version-history";
import { SecurityPanelWrapper } from "@/components/wiki/security-panel";
import { useGraphMetrics, useCallersCallees } from "@/lib/hooks/use-graph";
import type { ReaderPersona } from "@repowise-dev/ui/docs/reader-persona";
import type { DocPage } from "@repowise-dev/types/docs";
import type { PageResponse } from "@/lib/api/types";

function PercentileBar({ value, label }: { value: number; label: string }) {
  const pct = 100 - value;
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-[var(--color-text-tertiary)] shrink-0">{label}</span>
      <div className="flex items-center gap-1.5">
        <div className="w-16 h-1.5 rounded-full bg-[var(--color-bg-elevated)] overflow-hidden">
          <div
            className={cn(
              "h-full rounded-full",
              value >= 75 ? "bg-[var(--color-success)]" : value >= 50 ? "bg-[var(--color-warning)]" : "bg-[var(--color-text-tertiary)]",
            )}
            style={{ width: `${value}%` }}
          />
        </div>
        <span className={cn(
          "text-[10px] font-mono tabular-nums w-10 text-right",
          value >= 75 ? "text-[var(--color-success)]" : value >= 50 ? "text-[var(--color-warning)]" : "text-[var(--color-text-tertiary)]",
        )}>
          Top {pct}%
        </span>
      </div>
    </div>
  );
}

function DocsSidebar({ repoId, targetPath }: { repoId: string; targetPath: string }) {
  const nodeId = targetPath;
  const { metrics, isLoading: metricsLoading } = useGraphMetrics(repoId, nodeId);
  const symbolNodeId = targetPath.includes("::") ? targetPath : null;
  const { data: callData } = useCallersCallees(repoId, symbolNodeId);

  if (metricsLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-3/4" />
      </div>
    );
  }

  // No graph data — render nothing rather than announcing the absence.
  if (!metrics) return null;

  return (
    <div className="space-y-4">
      {/* Graph Metrics */}
      <div>
        <div className="flex items-center gap-1.5 mb-2">
          <Activity className="h-3 w-3 text-[var(--color-text-tertiary)]" />
          <span className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
            Importance
          </span>
        </div>
        <div className="space-y-2">
          <PercentileBar value={metrics.pagerank_percentile} label="PageRank" />
          <PercentileBar value={metrics.betweenness_percentile} label="Centrality" />
          <div className="flex items-center justify-between">
            <span className="text-xs text-[var(--color-text-tertiary)]">Degree</span>
            <span className="text-xs font-mono text-[var(--color-text-secondary)]">
              {metrics.in_degree} in &middot; {metrics.out_degree} out
            </span>
          </div>
          {metrics.is_entry_point && (
            <Badge variant="accent" className="text-[10px]">Entry Point</Badge>
          )}
        </div>
      </div>

      {/* Community */}
      {metrics.community_label && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Network className="h-3 w-3 text-[var(--color-text-tertiary)]" />
            <span className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
              Community
            </span>
          </div>
          <Link
            href={`/repos/${repoId}/architecture?view=graph&colorMode=community`}
            className="inline-flex items-center gap-1 text-xs text-[var(--color-accent)] hover:underline"
          >
            {metrics.community_label}
            <ArrowRight className="h-2.5 w-2.5" />
          </Link>
        </div>
      )}

      {/* Callers/Callees (for symbol pages) */}
      {callData && (callData.caller_count > 0 || callData.callee_count > 0) && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <GitBranch className="h-3 w-3 text-[var(--color-text-tertiary)]" />
            <span className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
              Call Graph
            </span>
          </div>
          {callData.caller_count > 0 && (
            <div className="mb-2">
              <p className="text-[10px] text-[var(--color-text-tertiary)] mb-1">Called by ({callData.caller_count})</p>
              {callData.callers.slice(0, 5).map((c) => (
                <p key={c.symbol_id} className="text-xs font-mono text-[var(--color-text-secondary)] truncate pl-2" title={c.symbol_id}>
                  {c.name}
                </p>
              ))}
            </div>
          )}
          {callData.callee_count > 0 && (
            <div>
              <p className="text-[10px] text-[var(--color-text-tertiary)] mb-1">Calls ({callData.callee_count})</p>
              {callData.callees.slice(0, 5).map((c) => (
                <p key={c.symbol_id} className="text-xs font-mono text-[var(--color-text-secondary)] truncate pl-2" title={c.symbol_id}>
                  {c.name}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Consolidated "At a glance" signals for a file page — hotspot, ownership,
 * churn, bus factor — pulled from the git-metadata endpoint so the reader
 * doesn't have to leave the doc to learn how risky/owned a file is.
 * Renders nothing for non-file pages or files without git history.
 */
function AtAGlance({ repoId, targetPath }: { repoId: string; targetPath: string }) {
  const isFile =
    !!targetPath &&
    !targetPath.includes("::") &&
    !targetPath.startsWith("onboarding/") &&
    !targetPath.startsWith("layer:");
  const { data } = useSWR(
    isFile ? `git-meta:${repoId}:${targetPath}` : null,
    () => getGitMetadata(repoId, targetPath),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  if (!data || data.commit_count_total === 0) return null;

  const churnTop = Math.max(1, 100 - Math.round(data.churn_percentile));
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-2">
        <Flame className="h-3 w-3 text-[var(--color-text-tertiary)]" />
        <span className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
          At a glance
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {data.is_hotspot && (
          <Badge variant="outline" className="text-[10px] border-[var(--color-warning)]/40 text-[var(--color-warning)]">
            <Flame className="h-2.5 w-2.5 mr-1" />
            Hotspot · top {churnTop}%
          </Badge>
        )}
        {data.is_stable && !data.is_hotspot && (
          <Badge variant="outline" className="text-[10px]">Stable</Badge>
        )}
        {data.bus_factor === 1 && (
          <Badge variant="outline" className="text-[10px] border-[var(--color-warning)]/40 text-[var(--color-warning)]">
            Bus factor 1
          </Badge>
        )}
      </div>
      <div className="mt-2 space-y-1 text-xs text-[var(--color-text-secondary)]">
        {data.primary_owner_name && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[var(--color-text-tertiary)]">Owner</span>
            <span className="truncate" title={data.primary_owner_name}>
              {data.primary_owner_name}
              {data.primary_owner_commit_pct != null &&
                ` · ${Math.round(data.primary_owner_commit_pct * 100)}%`}
            </span>
          </div>
        )}
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--color-text-tertiary)]">Commits (90d)</span>
          <span className="font-mono">{data.commit_count_90d}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--color-text-tertiary)]">Contributors</span>
          <span className="font-mono">{data.contributor_count}</span>
        </div>
        {data.agent_authored_pct != null && (data.agent_commit_count ?? 0) > 0 && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[var(--color-text-tertiary)]">Agent-authored</span>
            <span className="font-mono">
              {Math.round(data.agent_authored_pct * 100)}% · {data.agent_commit_count}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

interface DocsViewerProps {
  page: PageResponse | null;
  /** Full page list — powers hierarchical breadcrumbs and prev/next. */
  pages?: PageResponse[];
  repoId: string;
  isLoading?: boolean;
  /** Select another page in-place (breadcrumb / prev-next / wiki links). */
  onSelectPage?: (page: PageResponse) => void;
  persona: ReaderPersona;
  sidebarOpen: boolean;
}

/**
 * Web data wrapper around the presentational ``DocsReader`` ui shell. Owns the
 * data-bound rail sections (git "at a glance", graph intelligence, version
 * history) and the Next route shape; the reader layout itself lives in ui so
 * the redesign ships through the package bump.
 */
export function DocsViewer({
  page,
  pages = [],
  repoId,
  isLoading,
  onSelectPage,
  persona,
  sidebarOpen,
}: DocsViewerProps) {
  const buildPageHref = useCallback(
    (pageId: string) =>
      `/repos/${repoId}/docs?page=${encodeURIComponent(pageId)}`,
    [repoId],
  );

  // Fall back to the wiki deep-link route only when the target isn't in the
  // loaded list (which itself opens the unified reader via redirect).
  const onNavigatePageId = useCallback(
    (pageId: string) => {
      window.location.href = `/repos/${repoId}/wiki/${encodeURIComponent(pageId)}`;
    },
    [repoId],
  );

  const hasTargetPath = !!page?.target_path;
  const targetPath = page?.target_path ?? "";
  // Security findings only make sense for real source files.
  const isFilePath =
    hasTargetPath &&
    !targetPath.includes("::") &&
    !targetPath.startsWith("onboarding/") &&
    !targetPath.startsWith("layer:");

  return (
    <DocsReader
      page={page as unknown as DocPage | null}
      pages={pages as unknown as DocPage[]}
      repoId={repoId}
      isLoading={isLoading}
      onSelectPage={onSelectPage as ((p: DocPage) => void) | undefined}
      onNavigatePageId={onNavigatePageId}
      persona={persona}
      sidebarOpen={sidebarOpen}
      buildPageHref={buildPageHref}
      LinkComponent={Link}
      versionHistorySlot={
        page ? (
          <VersionHistoryWrapper
            pageId={page.id}
            currentVersion={page.version}
            currentContent={page.content}
          />
        ) : undefined
      }
      intelligenceSlot={
        hasTargetPath ? (
          <>
            <AtAGlance repoId={repoId} targetPath={targetPath} />
            <DocsSidebar repoId={repoId} targetPath={targetPath} />
            {isFilePath && (
              <div>
                <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
                  Security signals
                </p>
                <SecurityPanelWrapper repoId={repoId} filePath={targetPath} />
              </div>
            )}
          </>
        ) : undefined
      }
    />
  );
}
