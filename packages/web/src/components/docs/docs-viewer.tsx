"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  FileText,
  Clock,
  Cpu,
  Hash,
  ExternalLink,
  Download,
  StickyNote,
  ArrowRight,
  RefreshCw,
  Loader2,
  PanelRight,
  PanelRightClose,
  Network,
  Activity,
  GitBranch,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { WikiMarkdown } from "@repowise-dev/ui/wiki/wiki-markdown";
import { VersionHistoryWrapper } from "@/components/wiki/version-history";
import { ConfidenceBadge } from "@repowise-dev/ui/wiki/confidence-badge";
import { Button } from "@repowise-dev/ui/ui/button";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { ScrollArea } from "@repowise-dev/ui/ui/scroll-area";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { formatRelativeTime, formatTokens } from "@repowise-dev/ui/lib/format";
import { downloadTextFile } from "@/lib/utils/download";
import { useGraphMetrics, useCallersCallees } from "@/lib/hooks/use-graph";
import type { PageResponse } from "@/lib/api/types";

function PercentileBar({ value, label }: { value: number; label: string }) {
  const pct = 100 - value;
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[11px] text-[var(--color-text-tertiary)] shrink-0">{label}</span>
      <div className="flex items-center gap-1.5">
        <div className="w-16 h-1.5 rounded-full bg-[var(--color-bg-elevated)] overflow-hidden">
          <div
            className={cn(
              "h-full rounded-full",
              value >= 75 ? "bg-green-400" : value >= 50 ? "bg-yellow-400" : "bg-[var(--color-text-tertiary)]",
            )}
            style={{ width: `${value}%` }}
          />
        </div>
        <span className={cn(
          "text-[10px] font-mono tabular-nums w-10 text-right",
          value >= 75 ? "text-green-400" : value >= 50 ? "text-yellow-400" : "text-[var(--color-text-tertiary)]",
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
      <div className="space-y-3 p-3">
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-3/4" />
      </div>
    );
  }

  if (!metrics) {
    return (
      <div className="p-3">
        <p className="text-[11px] text-[var(--color-text-tertiary)] italic">No graph data available</p>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-3">
      {/* Graph Metrics */}
      <div>
        <div className="flex items-center gap-1.5 mb-2">
          <Activity className="h-3 w-3 text-[var(--color-text-tertiary)]" />
          <span className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
            Importance
          </span>
        </div>
        <div className="space-y-2">
          <PercentileBar value={metrics.pagerank_percentile} label="PageRank" />
          <PercentileBar value={metrics.betweenness_percentile} label="Centrality" />
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-[var(--color-text-tertiary)]">Degree</span>
            <span className="text-[11px] font-mono text-[var(--color-text-secondary)]">
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
            <span className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
              Community
            </span>
          </div>
          <Link
            href={`/repos/${repoId}/graph?colorMode=community`}
            className="inline-flex items-center gap-1 text-[11px] text-[var(--color-accent)] hover:underline"
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
            <span className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
              Call Graph
            </span>
          </div>
          {callData.caller_count > 0 && (
            <div className="mb-2">
              <p className="text-[10px] text-[var(--color-text-tertiary)] mb-1">Called by ({callData.caller_count})</p>
              {callData.callers.slice(0, 5).map((c) => (
                <p key={c.symbol_id} className="text-[11px] font-mono text-[var(--color-text-secondary)] truncate pl-2" title={c.symbol_id}>
                  {c.name}
                </p>
              ))}
            </div>
          )}
          {callData.callee_count > 0 && (
            <div>
              <p className="text-[10px] text-[var(--color-text-tertiary)] mb-1">Calls ({callData.callee_count})</p>
              {callData.callees.slice(0, 5).map((c) => (
                <p key={c.symbol_id} className="text-[11px] font-mono text-[var(--color-text-secondary)] truncate pl-2" title={c.symbol_id}>
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

interface DocsViewerProps {
  page: PageResponse | null;
  repoId: string;
  isLoading?: boolean;
}

export function DocsViewer({ page, repoId, isLoading }: DocsViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Scroll to top when page changes
  useEffect(() => {
    scrollRef.current?.scrollTo(0, 0);
  }, [page?.id]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-5 w-5 animate-spin text-[var(--color-accent-primary)]" />
      </div>
    );
  }

  if (!page) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
        <div className="rounded-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)] p-4">
          <FileText className="h-8 w-8 text-[var(--color-text-tertiary)]" />
        </div>
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Select a page
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)] max-w-sm">
            Choose a file or module from the tree to view its AI-generated documentation.
          </p>
        </div>
      </div>
    );
  }

  const hasTargetPath = !!page.target_path;

  return (
    <div className="flex h-full">
      {/* Main content */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Sticky header */}
        <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)]/95 backdrop-blur px-4 sm:px-6 py-2.5 flex-wrap sm:flex-nowrap shrink-0">
          {/* Path breadcrumb */}
          <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)] min-w-0 flex-1">
            <span className="font-mono truncate text-[var(--color-text-secondary)]">
              {page.target_path || page.page_type}
            </span>
          </div>

          {/* Confidence */}
          <ConfidenceBadge
            score={page.confidence}
            status={page.freshness_status}
            showScore
          />

          {/* Provider */}
          <Badge variant="outline" className="font-mono text-[10px] hidden sm:flex shrink-0">
            <Cpu className="h-2.5 w-2.5 mr-1" />
            <span className="truncate max-w-[100px]">{page.model_name}</span>
          </Badge>

          {/* Download as markdown */}
          <button
            onClick={() => {
              const filename = (page.target_path || page.title).replace(/\//g, "_") + ".md";
              const header = `# ${page.title}\n\n> Path: ${page.target_path}\n\n`;
              downloadTextFile(header + page.content, filename);
            }}
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)] transition-colors shrink-0"
            title="Download as Markdown"
          >
            <Download className="h-3.5 w-3.5" />
          </button>

          {/* Open full page link */}
          <Link
            href={`/repos/${repoId}/wiki/${encodeURIComponent(page.id)}`}
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)] transition-colors shrink-0"
            title="Open full page"
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </Link>

          {/* Sidebar toggle */}
          {hasTargetPath && (
            <button
              onClick={() => setSidebarOpen((o) => !o)}
              className={cn(
                "transition-colors shrink-0",
                sidebarOpen
                  ? "text-[var(--color-accent)] hover:text-[var(--color-accent)]"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)]",
              )}
              title={sidebarOpen ? "Hide insights" : "Show insights"}
            >
              {sidebarOpen ? (
                <PanelRightClose className="h-3.5 w-3.5" />
              ) : (
                <PanelRight className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>

        {/* Content */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="px-4 sm:px-6 py-6 max-w-[768px] mx-auto">
            {/* Title */}
            <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 break-words">
              {page.title}
            </h1>

            {/* Meta row */}
            <div className="flex items-center gap-3 text-[10px] text-[var(--color-text-tertiary)] mb-6">
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {formatRelativeTime(page.updated_at)}
              </span>
              <span>v{page.version}</span>
              <span className="font-mono">
                {formatTokens(page.input_tokens)} in Â· {formatTokens(page.output_tokens)} out
              </span>
            </div>

            {/* Human notes */}
            {page.human_notes && (
              <div className="mb-4 rounded-lg border border-[var(--color-border-accent)] bg-[var(--color-accent-blue)]/5 px-4 py-3">
                <div className="flex items-center gap-1.5 mb-1.5">
                  <StickyNote className="h-3.5 w-3.5 text-[var(--color-accent-blue)]" />
                  <span className="text-xs font-medium text-[var(--color-accent-blue)] uppercase tracking-wider">
                    Human Notes
                  </span>
                </div>
                <p className="text-sm text-[var(--color-text-secondary)] whitespace-pre-wrap leading-relaxed">
                  {page.human_notes}
                </p>
              </div>
            )}

            {/* Markdown content */}
            <article className="prose prose-invert max-w-none leading-relaxed overflow-hidden">
              <WikiMarkdown content={page.content} />
            </article>

            {/* Version history */}
            <div className="mt-8">
              <VersionHistoryWrapper
                pageId={page.id}
                currentVersion={page.version}
                currentContent={page.content}
              />
            </div>

            {/* Metadata warnings */}
            {Array.isArray(page.metadata?.hallucination_warnings) && (page.metadata.hallucination_warnings as string[]).length > 0 && (
              <div className="mt-4 rounded-lg border border-amber-400/30 bg-amber-50/5 px-4 py-3">
                <p className="text-xs font-medium text-amber-400 mb-1.5">
                  Possible inaccuracies detected
                </p>
                <ul className="space-y-0.5">
                  {(page.metadata.hallucination_warnings as string[]).map((w, i) => (
                    <li key={i} className="text-xs text-amber-300/80 font-mono">
                      {String(w)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Right sidebar â€” Graph Intelligence */}
      {hasTargetPath && sidebarOpen && (
        <div className="hidden lg:flex flex-col border-l border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shrink-0 w-[240px] overflow-auto">
          <DocsSidebar repoId={repoId} targetPath={page.target_path} />
        </div>
      )}
    </div>
  );
}
