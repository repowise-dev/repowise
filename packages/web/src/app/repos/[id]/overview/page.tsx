import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Hash } from "lucide-react";
import { getRepo, getRepoStats } from "@/lib/api/repos";
import { getGitSummary, getHotspots, getOwnership } from "@/lib/api/git";
import { getDeadCodeSummary, listDeadCode } from "@/lib/api/dead-code";
import { listDecisions, getDecisionHealth } from "@/lib/api/decisions";
import { getGraph, getModuleGraph, getCommunities, getExecutionFlows } from "@/lib/api/graph";
import { getProviders } from "@/lib/api/providers";
import { listJobs } from "@/lib/api/jobs";
import { getKnowledgeMap } from "@/lib/api/knowledge-map";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
import { HealthScoreRing } from "@repowise-dev/ui/dashboard/health-score-ring";
import { AttentionPanel } from "@repowise-dev/ui/dashboard/attention-panel";
import { QuickActionsWrapper as QuickActions } from "@/components/dashboard/quick-actions-wrapper";
import { LanguageDonut } from "@repowise-dev/ui/dashboard/language-donut";
import { computeHealthScore, buildAttentionItems, aggregateLanguages } from "@/lib/utils/health-score";
import { HotspotsMini } from "@repowise-dev/ui/dashboard/hotspots-mini";
import { DecisionsTimeline } from "@repowise-dev/ui/dashboard/decisions-timeline";
import { ModuleOverviewGrid } from "@repowise-dev/ui/dashboard/module-overview-grid";
import { CommunitySummaryGridWrapper as CommunitySummaryGrid } from "@/components/dashboard/community-summary-grid-wrapper";
import { ExecutionFlowsPanel } from "@repowise-dev/ui/dashboard/execution-flows-panel";
import { BusFactorPanel } from "@repowise-dev/ui/git/bus-factor-panel";
import { CommitCategorySparkline } from "@repowise-dev/ui/git/commit-category-sparkline";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import type {
  RepoStatsResponse,
  GitSummaryResponse,
  HotspotResponse,
  OwnershipEntry,
  DeadCodeSummaryResponse,
  DecisionRecordResponse,
  DecisionHealthResponse,
  GraphExportResponse,
  ModuleGraphResponse,
  CommunitySummaryItem,
  ExecutionFlowsResponse,
} from "@/lib/api/types";

export const metadata: Metadata = { title: "Overview" };

interface Props {
  params: Promise<{ id: string }>;
}

// Each fetch wrapped to return null on failure — the dashboard degrades gracefully
async function safeFetch<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

export default async function OverviewPage({ params }: Props) {
  const { id } = await params;

  const repo = await safeFetch(() => getRepo(id));
  if (!repo) notFound();

  // Fetch all data in parallel — each independently failable
  const [stats, gitSummary, hotspots, ownership, deadCodeSummary, deadCodeSafe, decisions, decisionHealth, graph, moduleGraph, providers, completedJobs, knowledgeMap, communities, executionFlows] =
    await Promise.all([
      safeFetch(() => getRepoStats(id)),
      safeFetch(() => getGitSummary(id)),
      safeFetch(() => getHotspots(id, 50)),
      safeFetch(() => getOwnership(id, "module")),
      safeFetch(() => getDeadCodeSummary(id)),
      safeFetch(() => listDeadCode(id, { safe_only: true, status: "active", limit: 50 })),
      safeFetch(() => listDecisions(id, { limit: 10 })),
      safeFetch(() => getDecisionHealth(id)),
      safeFetch(() => getGraph(id)),
      safeFetch(() => getModuleGraph(id)),
      safeFetch(() => getProviders()),
      safeFetch(() => listJobs({ repo_id: id, limit: 20, status: "completed" })),
      safeFetch(() => getKnowledgeMap(id)),
      safeFetch(() => getCommunities(id)),
      safeFetch(() => getExecutionFlows(id, { top_n: 5, max_depth: 5 })),
    ]);

  // Find timestamps for last sync and last full re-index from completed jobs
  const lastSyncJob = completedJobs?.find((j) => !j.config?.mode || j.config.mode === "sync");
  const lastResyncJob = completedJobs?.find((j) => j.config?.mode === "full_resync");

  // Compute health score
  const siloCount = ownership?.filter((o) => o.is_silo).length ?? 0;
  const healthScore = computeHealthScore({
    docCoveragePct: stats?.doc_coverage_pct ?? 0,
    freshnessScore: stats?.freshness_score ?? 0,
    deadExportCount: stats?.dead_export_count ?? 0,
    symbolCount: stats?.symbol_count ?? 1,
    hotspotCount: gitSummary?.hotspot_count ?? 0,
    totalFiles: gitSummary?.total_files ?? 1,
    siloCount,
    totalModules: ownership?.length ?? 1,
  });

  // Build attention items
  const attentionItems = buildAttentionItems({
    staleDecisions: decisionHealth?.stale_decisions ?? [],
    proposedDecisions: decisionHealth?.proposed_awaiting_review ?? [],
    ungovernedHotspots: decisionHealth?.ungoverned_hotspots ?? [],
    siloModules: ownership?.filter((o) => o.is_silo) ?? [],
    deadCodeSafe: deadCodeSafe ?? [],
  });

  // Aggregate language distribution from graph nodes
  const langDistribution = graph ? aggregateLanguages(graph.nodes) : {};

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      {/* ── Hero: Health Score + Repo Info + Quick Actions ── */}
      <div className="flex flex-col sm:flex-row items-start gap-6">
        <HealthScoreRing
          score={healthScore.score}
          components={healthScore.components}
          note={healthScore.note}
        />

        <div className="flex-1 min-w-0 space-y-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-[var(--color-text-primary)] truncate">
                {repo.name}
              </h1>
              {repo.head_commit && (
                <Badge variant="outline" className="text-[10px] h-5 shrink-0">
                  <Hash className="h-2.5 w-2.5" />
                  {repo.head_commit.slice(0, 7)}
                </Badge>
              )}
              <Badge variant="outline" className="text-[10px] h-5 shrink-0">
                {repo.default_branch}
              </Badge>
            </div>
            <p className="text-xs font-mono text-[var(--color-text-tertiary)] truncate mt-0.5">
              {repo.local_path}
            </p>
          </div>

          {/* Quick actions */}
          <QuickActions
            repoId={id}
            repoName={repo.name}
            pageCount={stats?.file_count ?? 0}
            modelName={providers?.active.model ?? ""}
            lastSyncAt={lastSyncJob?.finished_at ?? null}
            lastResyncAt={lastResyncJob?.finished_at ?? null}
          />

          {/* Key metrics strip */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            <StatCard
              label="Files"
              value={stats ? formatNumber(stats.file_count) : "–"}
              href={`/repos/${id}/graph`}
              className="!p-0 [&>div]:!p-3"
            />
            <StatCard
              label="Symbols"
              value={stats ? formatNumber(stats.symbol_count) : "–"}
              href={`/repos/${id}/symbols`}
              className="!p-0 [&>div]:!p-3"
            />
            <StatCard
              label="Entry Points"
              value={stats ? formatNumber(stats.entry_point_count) : "–"}
              href={`/repos/${id}/graph?viewMode=architecture`}
              className="!p-0 [&>div]:!p-3"
            />
            <StatCard
              label="Doc Coverage"
              value={stats ? `${Math.round(stats.doc_coverage_pct)}%` : "–"}
              href={`/repos/${id}/docs/coverage`}
              className="!p-0 [&>div]:!p-3"
            />
            <StatCard
              label="Dead Exports"
              value={stats ? formatNumber(stats.dead_export_count) : "–"}
              description={
                deadCodeSummary
                  ? `${formatNumber(deadCodeSummary.deletable_lines)} deletable lines`
                  : undefined
              }
              href={`/repos/${id}/dead-code`}
              className="!p-0 [&>div]:!p-3"
            />
          </div>
        </div>
      </div>

      {/* ── Main Grid ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Left column — Attention + Hotspots */}
        <div className="space-y-4 lg:col-span-2">
          <AttentionPanel items={attentionItems} repoId={id} />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <HotspotsMini hotspots={hotspots ?? []} repoId={id} />
            <DecisionsTimeline
              decisions={
                decisions
                  ? [...decisions].sort(
                      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
                    )
                  : []
              }
              repoId={id}
            />
          </div>
        </div>

        {/* Right column — Visualizations */}
        <div className="space-y-4">
          <LanguageDonut distribution={langDistribution} />
          <a
            href={`/repos/${id}/graph?colorMode=language`}
            className="block text-[10px] text-[var(--color-accent-primary)] hover:underline text-right -mt-1"
          >
            View in Graph →
          </a>
        </div>
      </div>

      {/* ── Git Insights ── */}
      {hotspots && hotspots.length > 0 && (() => {
        const aggregatedCategories: Record<string, number> = {};
        for (const h of hotspots) {
          for (const [cat, count] of Object.entries(h.commit_categories ?? {})) {
            aggregatedCategories[cat] = (aggregatedCategories[cat] || 0) + count;
          }
        }
        const hasCategories = Object.values(aggregatedCategories).some((v) => v > 0);

        return hasCategories ? (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-[var(--color-text-secondary)] uppercase tracking-wider">
                Git Insights
              </h2>
              <a href={`/repos/${id}/hotspots`} className="text-[10px] text-[var(--color-accent-primary)] hover:underline">
                View all →
              </a>
            </div>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Commit Activity</CardTitle>
              </CardHeader>
              <CardContent>
                <CommitCategorySparkline categories={aggregatedCategories} />
                <div className="flex items-center gap-4 mt-2 text-[10px] text-[var(--color-text-tertiary)]">
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: "#5b9cf6" }} /> Feature</span>
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: "#ef4444" }} /> Fix</span>
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: "#a855f7" }} /> Refactor</span>
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: "#f59520" }} /> Dependency</span>
                </div>
              </CardContent>
            </Card>
          </div>
        ) : null;
      })()}

      {/* ── Architecture ── */}
      {moduleGraph && moduleGraph.nodes.length > 0 && (
        <ModuleOverviewGrid
          nodes={moduleGraph.nodes}
          edges={moduleGraph.edges}
          repoId={id}
        />
      )}

      {/* ── Graph Intelligence: Communities & Execution Flows ── */}
      {((communities && communities.length > 0) || (executionFlows && executionFlows.flows.length > 0)) && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-[var(--color-text-secondary)] uppercase tracking-wider">
              Graph Intelligence
            </h2>
            <a href={`/repos/${id}/graph?colorMode=community`} className="text-[10px] text-[var(--color-accent-primary)] hover:underline">
              View all →
            </a>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {communities && communities.length > 0 && (
              <CommunitySummaryGrid communities={communities} repoId={id} />
            )}
            {executionFlows && executionFlows.flows.length > 0 && (
              <ExecutionFlowsPanel flows={executionFlows.flows} repoId={id} />
            )}
          </div>
        </div>
      )}

      {/* ── Ownership & Knowledge ── */}
      {(hotspots || knowledgeMap) && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-[var(--color-text-secondary)] uppercase tracking-wider">
              Ownership & Knowledge
            </h2>
            <a href={`/repos/${id}/ownership`} className="text-[10px] text-[var(--color-accent-primary)] hover:underline">
              View all →
            </a>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            {hotspots && hotspots.length > 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium">Bus Factor</CardTitle>
                </CardHeader>
                <CardContent>
                  <BusFactorPanel hotspots={hotspots} />
                </CardContent>
              </Card>
            )}

            {knowledgeMap && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium">Knowledge Silos</CardTitle>
                </CardHeader>
                <CardContent>
                  {knowledgeMap.knowledge_silos.length === 0 ? (
                    <p className="text-xs text-[var(--color-text-tertiary)]">No silos detected — good bus factor!</p>
                  ) : (
                    <div className="space-y-1">
                      <p className="text-xs text-[var(--color-text-secondary)] mb-2">
                        {formatNumber(knowledgeMap.knowledge_silos.length)} file
                        {knowledgeMap.knowledge_silos.length === 1 ? "" : "s"} with &gt;80% single-owner concentration
                      </p>
                      <ul className="space-y-1">
                        {knowledgeMap.knowledge_silos.slice(0, 3).map((silo) => (
                          <li key={silo.file_path}>
                            <a
                              href={`/repos/${id}/graph?node=${encodeURIComponent(silo.file_path)}`}
                              className="flex items-center justify-between gap-2 -mx-2 px-2 py-0.5 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
                            >
                              <p className="text-[11px] font-mono text-[var(--color-text-primary)] truncate min-w-0">
                                {silo.file_path}
                              </p>
                              <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0">
                                {Math.round(silo.owner_pct * 100)}%
                              </span>
                            </a>
                          </li>
                        ))}
                        {knowledgeMap.knowledge_silos.length > 3 && (
                          <li>
                            <a href={`/repos/${id}/ownership`} className="text-[10px] text-[var(--color-accent-primary)] hover:underline">
                              +{knowledgeMap.knowledge_silos.length - 3} more
                            </a>
                          </li>
                        )}
                      </ul>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            {knowledgeMap && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium">Onboarding Targets</CardTitle>
                </CardHeader>
                <CardContent>
                  {knowledgeMap.onboarding_targets.length === 0 ? (
                    <p className="text-xs text-[var(--color-text-tertiary)]">No graph data available.</p>
                  ) : (
                    <ul className="space-y-2">
                      {knowledgeMap.onboarding_targets.slice(0, 5).map((target) => (
                        <li key={target.path}>
                          <a
                            href={`/repos/${id}/graph?node=${encodeURIComponent(target.path)}`}
                            className="block -mx-2 px-2 py-0.5 rounded hover:bg-[var(--color-bg-elevated)] transition-colors space-y-0.5"
                          >
                            <p className="text-[11px] font-mono text-[var(--color-text-primary)] truncate">
                              {target.path}
                            </p>
                            <p className="text-[10px] text-[var(--color-text-tertiary)]">
                              pagerank {target.pagerank.toFixed(4)} · {formatNumber(target.doc_words)} doc words
                            </p>
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
