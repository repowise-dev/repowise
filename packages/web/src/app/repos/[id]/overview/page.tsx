import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Hash, Activity } from "lucide-react";
import { getRepo, getRepoStats } from "@/lib/api/repos";
import { getGitSummary, getHotspots, getOwnership } from "@/lib/api/git";
import { getDeadCodeSummary, listDeadCode } from "@/lib/api/dead-code";
import { listDecisions, getDecisionHealth } from "@/lib/api/decisions";
import { getGraph, getModuleGraph, getCommunities, getExecutionFlows } from "@/lib/api/graph";
import { getProviders } from "@/lib/api/providers";
import { listJobs } from "@/lib/api/jobs";
import { getKnowledgeMap } from "@/lib/api/knowledge-map";
import { Badge } from "@repowise/ui/ui/badge";
import { StatCard } from "@repowise/ui/shared/stat-card";
import { HealthScoreRing } from "@/components/dashboard/health-score-ring";
import { AttentionPanel } from "@/components/dashboard/attention-panel";
import { QuickActions } from "@/components/dashboard/quick-actions";
import { OwnershipTreemap } from "@/components/dashboard/ownership-treemap";
import { LanguageDonut } from "@/components/dashboard/language-donut";
import { computeHealthScore, buildAttentionItems, aggregateLanguages } from "@/lib/utils/health-score";
import { HotspotsMini } from "@/components/dashboard/hotspots-mini";
import { DecisionsTimeline } from "@/components/dashboard/decisions-timeline";
import { ModuleMinimap } from "@/components/dashboard/module-minimap";
import { CommunitySummaryGrid } from "@/components/dashboard/community-summary-grid";
import { ExecutionFlowsPanel } from "@/components/dashboard/execution-flows-panel";
import { DependencyHeatmap } from "@/components/dashboard/dependency-heatmap";
import { BusFactorPanel } from "@repowise/ui/git/bus-factor-panel";
import { ChurnHistogram } from "@repowise/ui/git/churn-histogram";
import { CommitCategoryDonut } from "@repowise/ui/git/commit-category-donut";
import { CommitCategorySparkline } from "@repowise/ui/git/commit-category-sparkline";
import { OwnershipTreemap as OwnershipTreemapGit } from "@repowise/ui/git/ownership-treemap";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise/ui/ui/card";
import { formatNumber } from "@repowise/ui/lib/format";
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
        <HealthScoreRing score={healthScore} />

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
              value={stats ? formatNumber(stats.file_count) : "—"}
              className="!p-0 [&>div]:!p-3"
            />
            <StatCard
              label="Symbols"
              value={stats ? formatNumber(stats.symbol_count) : "—"}
              className="!p-0 [&>div]:!p-3"
            />
            <StatCard
              label="Entry Points"
              value={stats ? formatNumber(stats.entry_point_count) : "—"}
              className="!p-0 [&>div]:!p-3"
            />
            <StatCard
              label="Doc Coverage"
              value={stats ? `${Math.round(stats.doc_coverage_pct)}%` : "—"}
              className="!p-0 [&>div]:!p-3"
            />
            <StatCard
              label="Dead Exports"
              value={stats ? formatNumber(stats.dead_export_count) : "—"}
              description={
                deadCodeSummary
                  ? `${formatNumber(deadCodeSummary.deletable_lines)} deletable lines`
                  : undefined
              }
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
          {ownership && ownership.length > 0 && (
            <OwnershipTreemap entries={ownership} />
          )}
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

        return (
          <div className="space-y-4">
            <h2 className="text-sm font-medium text-[var(--color-text-secondary)] uppercase tracking-wider">
              Git Insights
            </h2>

            {/* Commit category sparkline (full width) */}
            {hasCategories && (
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
            )}

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              {/* Churn Histogram */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium">Churn Distribution</CardTitle>
                </CardHeader>
                <CardContent>
                  <ChurnHistogram hotspots={hotspots} />
                </CardContent>
              </Card>

              {/* Commit Category Donut */}
              {hasCategories && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium">Commit Categories</CardTitle>
                  </CardHeader>
                  <CardContent className="flex items-center justify-center">
                    <CommitCategoryDonut categories={aggregatedCategories} />
                  </CardContent>
                </Card>
              )}

              {/* Bus Factor */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium">Bus Factor</CardTitle>
                </CardHeader>
                <CardContent>
                  <BusFactorPanel hotspots={hotspots} />
                </CardContent>
              </Card>
            </div>

            {/* Ownership Treemap (git-level, file granularity) */}
            {ownership && ownership.length > 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium">Ownership Map</CardTitle>
                </CardHeader>
                <CardContent>
                  <OwnershipTreemapGit entries={ownership} />
                </CardContent>
              </Card>
            )}
          </div>
        );
      })()}

      {/* ── Module Architecture Map + Dependency Heatmap ── */}
      {moduleGraph && moduleGraph.nodes.length > 0 && (
        <div className="space-y-4">
          <ModuleMinimap
            nodes={moduleGraph.nodes}
            edges={moduleGraph.edges}
            repoId={id}
          />
          {moduleGraph.nodes.length >= 2 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Dependency Heatmap</CardTitle>
                <p className="text-xs text-[var(--color-text-tertiary)]">
                  Module-to-module dependency density — brighter cells indicate more connections
                </p>
              </CardHeader>
              <CardContent>
                <DependencyHeatmap moduleGraph={moduleGraph} />
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* ── Graph Intelligence: Communities & Execution Flows ── */}
      {((communities && communities.length > 0) || (executionFlows && executionFlows.flows.length > 0)) && (
        <div className="space-y-4">
          <h2 className="text-sm font-medium text-[var(--color-text-secondary)] uppercase tracking-wider">
            Graph Intelligence
          </h2>
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

      {/* ── Knowledge Map ── */}
      {knowledgeMap && (
        <div className="space-y-4">
          <h2 className="text-sm font-medium text-[var(--color-text-secondary)] uppercase tracking-wider">
            Knowledge Map
          </h2>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">

            {/* Top Owners */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Top Owners</CardTitle>
              </CardHeader>
              <CardContent>
                {knowledgeMap.top_owners.length === 0 ? (
                  <p className="text-xs text-[var(--color-text-tertiary)]">No ownership data available.</p>
                ) : (
                  <ul className="space-y-2">
                    {knowledgeMap.top_owners.slice(0, 5).map((owner) => (
                      <li key={owner.email} className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-xs font-medium text-[var(--color-text-primary)] truncate">
                            {owner.name || owner.email}
                          </p>
                          {owner.name && (
                            <p className="text-[10px] text-[var(--color-text-tertiary)] truncate">{owner.email}</p>
                          )}
                        </div>
                        <div className="text-right shrink-0">
                          <span className="text-xs font-mono text-[var(--color-text-secondary)]">
                            {formatNumber(owner.files_owned)} files
                          </span>
                          <span className="ml-2 text-[10px] text-[var(--color-text-tertiary)]">
                            {owner.percentage}%
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            {/* Knowledge Silos */}
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
                        <li key={silo.file_path} className="flex items-center justify-between gap-2">
                          <p className="text-[11px] font-mono text-[var(--color-text-primary)] truncate min-w-0">
                            {silo.file_path}
                          </p>
                          <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0">
                            {Math.round(silo.owner_pct * 100)}%
                          </span>
                        </li>
                      ))}
                      {knowledgeMap.knowledge_silos.length > 3 && (
                        <li className="text-[10px] text-[var(--color-text-tertiary)]">
                          +{knowledgeMap.knowledge_silos.length - 3} more
                        </li>
                      )}
                    </ul>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Onboarding Targets */}
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
                      <li key={target.path} className="space-y-0.5">
                        <p className="text-[11px] font-mono text-[var(--color-text-primary)] truncate">
                          {target.path}
                        </p>
                        <p className="text-[10px] text-[var(--color-text-tertiary)]">
                          pagerank {target.pagerank.toFixed(4)} · {formatNumber(target.doc_words)} doc words
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

          </div>
        </div>
      )}
    </div>
  );
}
