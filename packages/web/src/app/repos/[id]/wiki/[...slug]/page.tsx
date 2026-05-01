import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ApiClientError } from "@/lib/api/client";
import { getRepo } from "@/lib/api/repos";
import { getPageById, getPageVersions } from "@/lib/api/pages";
import { getGitMetadata } from "@/lib/api/git";
import { getGraphMetrics } from "@/lib/api/graph";
import { ConfidenceBadge } from "@/components/wiki/confidence-badge";
import { WikiRenderer } from "@/components/wiki/wiki-renderer";
import { TableOfContents } from "@/components/wiki/table-of-contents";
import { RegenerateButton } from "@/components/wiki/regenerate-button";
import { GitHistoryPanel } from "@/components/wiki/git-history-panel";
import { SecurityPanel } from "@/components/wiki/security-panel";
import { Badge } from "@repowise/ui/ui/badge";
import { Separator } from "@repowise/ui/ui/separator";
import { formatRelativeTime, formatTokens } from "@repowise/ui/lib/format";
import { CoChangeList } from "@repowise/ui/git/co-change-list";
import { Hash, Cpu, StickyNote, Network } from "lucide-react";
import type { GraphMetricsResponse } from "@/lib/api/types";

interface Props {
  params: Promise<{ id: string; slug: string[] }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id, slug } = await params;
  const pageId = slug.join("/");
  try {
    const page = await getPageById(pageId);
    return { title: page.title };
  } catch {
    return { title: "Wiki Page" };
  }
}

export default async function WikiPageRoute({ params }: Props) {
  const { id, slug } = await params;
  const pageId = slug.join("/");

  let page;
  try {
    page = await getPageById(pageId);
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) {
      notFound();
    }
    throw err;
  }

  const [repo, versions, gitMeta, graphMetricsResult] = await Promise.allSettled([
    getRepo(id),
    getPageVersions(pageId),
    page.target_path ? getGitMetadata(id, page.target_path) : Promise.reject(),
    page.target_path ? getGraphMetrics(id, page.target_path) : Promise.reject(),
  ]);

  const repoData = repo.status === "fulfilled" ? repo.value : null;
  const versionList = versions.status === "fulfilled" ? versions.value : [];
  const git = gitMeta.status === "fulfilled" ? gitMeta.value : null;
  const graphMetrics: GraphMetricsResponse | null =
    graphMetricsResult.status === "fulfilled" ? graphMetricsResult.value : null;

  return (
    <div className="flex h-full min-h-0">
      {/* Main content */}
      <div className="flex-1 min-w-0 overflow-auto">
        {/* Top bar */}
        <div className="sticky top-0 z-[var(--z-elevated)] flex items-center gap-2 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)]/95 backdrop-blur px-3 sm:px-6 py-2.5 flex-wrap sm:flex-nowrap">
          {/* Breadcrumb */}
          <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)] min-w-0 flex-1">
            {repoData && (
              <>
                <span className="hidden sm:block truncate max-w-[80px]">{repoData.name}</span>
                <span className="hidden sm:block">/</span>
              </>
            )}
            <span className="font-mono truncate text-[var(--color-text-secondary)] max-w-[180px] sm:max-w-none" title={page.target_path || page.page_type}>
              {page.target_path || page.page_type}
            </span>
          </div>

          {/* Confidence badge */}
          <ConfidenceBadge
            score={page.confidence}
            status={page.freshness_status}
            showScore
          />

          {/* Provider badge */}
          <Badge variant="outline" className="font-mono text-xs hidden sm:flex shrink-0">
            <Cpu className="h-3 w-3 mr-1" />
            <span className="truncate max-w-[200px]" title={`${page.provider_name}/${page.model_name}`}>{page.provider_name}/{page.model_name}</span>
          </Badge>

          {/* Commit */}
          {page.source_hash && (
            <Badge variant="outline" className="font-mono text-xs hidden md:flex shrink-0">
              <Hash className="h-3 w-3 mr-1" />
              {page.source_hash.slice(0, 7)}
            </Badge>
          )}

          {/* Regenerate */}
          <RegenerateButton pageId={page.id} repoId={id} />
        </div>

        {/* Page content */}
        <div className="px-4 sm:px-6 py-6 max-w-[768px] mx-auto">
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-4 break-words">
            {page.title}
          </h1>

          {page.human_notes && (
            <div className="mb-5 rounded-lg border border-[var(--color-border-accent)] bg-[var(--color-accent-blue)]/5 px-4 py-3">
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

          <article className="prose prose-invert max-w-none leading-relaxed overflow-hidden">
            <WikiRenderer content={page.content} />
          </article>

          {/* Hallucination warnings */}
          {Array.isArray(page.metadata?.hallucination_warnings) && (page.metadata.hallucination_warnings as string[]).length > 0 && (
            <div className="mt-6 rounded-lg border border-amber-400/30 bg-amber-50/5 px-4 py-3">
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

        {/* Bottom bar */}
        <div className="border-t border-[var(--color-border-default)] px-4 sm:px-6 py-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-[var(--color-text-tertiary)]">
          <span>Generated {formatRelativeTime(page.updated_at)}</span>
          <Separator orientation="vertical" className="h-4 hidden sm:block" />
          <span className="font-mono">
            {formatTokens(page.input_tokens)} in · {formatTokens(page.output_tokens)} out
          </span>
          {versionList.length > 0 && (
            <>
              <Separator orientation="vertical" className="h-4 hidden sm:block" />
              <span>v{page.version} ({versionList.length} versions)</span>
            </>
          )}
        </div>
      </div>

      {/* Context panel (right sidebar) */}
      <div
        className="hidden xl:flex flex-col border-l border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shrink-0"
        style={{ width: "280px" }}
      >
        <div className="overflow-auto flex-1 p-4 space-y-5">
          {/* Table of contents */}
          <TableOfContents content={page.content} />

          {/* Git history panel */}
          {git && (
            <div>
              <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
                Git History
              </p>
              <div className="space-y-1.5 mb-3">
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--color-text-tertiary)]">Commits (90d)</span>
                  <span className="text-[var(--color-text-secondary)] tabular-nums">
                    {git.commit_count_90d}
                  </span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--color-text-tertiary)]">Churn</span>
                  <span className="text-[var(--color-text-secondary)] tabular-nums">
                    {Math.round(git.churn_percentile)}th pct
                  </span>
                </div>
                {git.is_hotspot && (
                  <Badge variant="stale" className="text-xs">Hotspot</Badge>
                )}
              </div>
              <GitHistoryPanel git={git} />
            </div>
          )}

          {/* Graph Intelligence */}
          {graphMetrics && (
            <div>
              <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
                Graph Intelligence
              </p>
              <div className="space-y-1.5">
                {graphMetrics.community_label && (
                  <div className="flex justify-between text-xs">
                    <span className="text-[var(--color-text-tertiary)]">Community</span>
                    <Link
                      href={`/repos/${id}/graph?colorMode=community`}
                      className="text-[var(--color-accent)] hover:underline font-mono text-[11px]"
                    >
                      {graphMetrics.community_label}
                    </Link>
                  </div>
                )}
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--color-text-tertiary)]">PageRank</span>
                  <span className={`tabular-nums font-mono text-[11px] ${graphMetrics.pagerank_percentile >= 75 ? "text-green-400" : graphMetrics.pagerank_percentile >= 50 ? "text-yellow-400" : "text-[var(--color-text-secondary)]"}`}>
                    Top {100 - graphMetrics.pagerank_percentile}%
                  </span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--color-text-tertiary)]">Betweenness</span>
                  <span className={`tabular-nums font-mono text-[11px] ${graphMetrics.betweenness_percentile >= 75 ? "text-green-400" : graphMetrics.betweenness_percentile >= 50 ? "text-yellow-400" : "text-[var(--color-text-secondary)]"}`}>
                    Top {100 - graphMetrics.betweenness_percentile}%
                  </span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-[var(--color-text-tertiary)]">Degree</span>
                  <span className="text-[var(--color-text-secondary)] tabular-nums font-mono text-[11px]">
                    {graphMetrics.in_degree} in &middot; {graphMetrics.out_degree} out
                  </span>
                </div>
                {graphMetrics.is_entry_point && (
                  <Badge variant="accent" className="text-xs">Entry Point</Badge>
                )}
              </div>
            </div>
          )}

          {/* Security findings panel */}
          {page.target_path && (
            <div>
              <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
                Security Signals
              </p>
              <SecurityPanel repoId={id} filePath={page.target_path} />
            </div>
          )}

          {/* Page metadata */}
          <div>
            <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
              Page Info
            </p>
            <div className="space-y-1.5 text-xs">
              <div className="flex justify-between gap-2">
                <span className="text-[var(--color-text-tertiary)] shrink-0">Type</span>
                <span className="text-[var(--color-text-secondary)] font-mono truncate">{page.page_type}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-[var(--color-text-tertiary)] shrink-0">Version</span>
                <span className="text-[var(--color-text-secondary)] tabular-nums">v{page.version}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-[var(--color-text-tertiary)] shrink-0">Level</span>
                <span className="text-[var(--color-text-secondary)] tabular-nums">{page.generation_level}</span>
              </div>
              {Array.isArray(page.metadata?.hallucination_warnings) && (page.metadata.hallucination_warnings as string[]).length > 0 && (
                <div className="flex justify-between gap-2">
                  <span className="text-[var(--color-text-tertiary)] shrink-0">Warnings</span>
                  <Badge variant="stale" className="text-[10px]">
                    {(page.metadata.hallucination_warnings as string[]).length}
                  </Badge>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

