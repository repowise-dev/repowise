import type { Metadata } from "next";
import Link from "next/link";
import {
  Layers,
  FileText,
  Code2,
  BarChart3,
  Flame,
  Link2,
  GitMerge,
  ArrowRight,
} from "lucide-react";
import { getWorkspace, getWorkspaceCoChanges } from "@/lib/api/workspace";
import { StatCard } from "@/components/shared/stat-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RepoCard } from "@/components/workspace/repo-card";
import { CrossRepoSummary } from "@/components/workspace/cross-repo-summary";
import { CoChangeTable } from "@/components/workspace/co-change-table";
import { ContractTypeBadge } from "@/components/workspace/contract-type-badge";
import { formatNumber } from "@/lib/utils/format";

export const metadata: Metadata = { title: "Workspace" };

export const revalidate = 30;

async function safeFetch<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

export default async function WorkspaceDashboardPage() {
  const [workspace, coChanges] = await Promise.all([
    safeFetch(() => getWorkspace()),
    safeFetch(() => getWorkspaceCoChanges({ limit: 10 })),
  ]);

  const wsRepos = workspace?.repos ?? [];

  // Aggregate stats from per-repo data embedded in the workspace response
  let totalFiles = 0;
  let totalSymbols = 0;
  let totalCoveragePctSum = 0;
  let totalHotspots = 0;
  let reposWithStats = 0;

  for (const r of wsRepos) {
    totalFiles += r.file_count;
    totalSymbols += r.symbol_count;
    totalCoveragePctSum += r.doc_coverage_pct;
    totalHotspots += r.hotspot_count;
    if (r.file_count > 0) reposWithStats++;
  }
  const avgCoverage = reposWithStats > 0 ? Math.round(totalCoveragePctSum / reposWithStats) : 0;

  return (
    <div className="p-5 sm:p-8 space-y-8 max-w-[1200px]">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <Layers className="h-6 w-6 text-[var(--color-accent-primary)]" />
          <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">
            {workspace?.workspace_name ?? "Workspace"}
          </h1>
        </div>
        <p className="text-sm text-[var(--color-text-secondary)]">
          {workspace?.repos.length ?? 0} repositories
          {workspace?.workspace_root && (
            <span className="text-[var(--color-text-tertiary)]">
              {" "}&middot; {workspace.workspace_root}
            </span>
          )}
        </p>
      </div>

      {/* Aggregate Stats */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard
          label="Total Files"
          value={formatNumber(totalFiles)}
          icon={<FileText className="h-4 w-4" />}
        />
        <StatCard
          label="Total Symbols"
          value={formatNumber(totalSymbols)}
          icon={<Code2 className="h-4 w-4" />}
        />
        <StatCard
          label="Avg Coverage"
          value={`${avgCoverage}%`}
          icon={<BarChart3 className="h-4 w-4" />}
        />
        <StatCard
          label="Hotspots"
          value={totalHotspots}
          icon={<Flame className="h-4 w-4 text-orange-400" />}
        />
        <StatCard
          label="Pages"
          value={formatNumber(wsRepos.reduce((a, r) => a + r.page_count, 0))}
          icon={<Code2 className="h-4 w-4 text-[var(--color-text-tertiary)]" />}
        />
      </div>

      {/* Repo Cards */}
      <section>
        <h2 className="text-sm font-medium text-[var(--color-text-primary)] mb-3">
          Repositories
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {wsRepos.map((wsRepo) => (
              <RepoCard
                key={wsRepo.alias}
                repoId={wsRepo.repo_id ?? ""}
                alias={wsRepo.alias}
                name={wsRepo.alias}
                path={wsRepo.path}
                isPrimary={wsRepo.is_primary}
                stats={wsRepo.file_count > 0 ? {
                  file_count: wsRepo.file_count,
                  symbol_count: wsRepo.symbol_count,
                  doc_coverage_pct: wsRepo.doc_coverage_pct,
                  entry_point_count: 0,
                  freshness_score: 0,
                  dead_export_count: 0,
                } : null}
                gitSummary={wsRepo.file_count > 0 ? {
                  total_files: wsRepo.file_count,
                  hotspot_count: wsRepo.hotspot_count,
                  stable_count: 0,
                  average_churn_percentile: 0,
                  top_owners: [],
                } : null}
              />
          ))}
        </div>
      </section>

      {/* Cross-Repo Intelligence */}
      {(workspace?.cross_repo_summary || workspace?.contract_summary) && (
        <section>
          <h2 className="text-sm font-medium text-[var(--color-text-primary)] mb-3">
            Cross-Repo Intelligence
          </h2>
          <CrossRepoSummary
            crossRepo={workspace?.cross_repo_summary ?? null}
            contracts={workspace?.contract_summary ?? null}
          />

          {/* Contract type breakdown */}
          {workspace?.contract_summary && workspace.contract_summary.total_links > 0 && (
            <Card className="mt-3">
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm font-medium flex items-center gap-2">
                    <Link2 className="h-4 w-4 text-[var(--color-accent-primary)]" />
                    API Contracts
                  </CardTitle>
                  <Link
                    href="/workspace/contracts"
                    className="text-xs text-[var(--color-accent-primary)] hover:underline flex items-center gap-1"
                  >
                    View all <ArrowRight className="h-3 w-3" />
                  </Link>
                </div>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="flex items-center gap-3">
                  {Object.entries(workspace.contract_summary.by_type).map(([type, count]) => (
                    <div key={type} className="flex items-center gap-1.5">
                      <ContractTypeBadge type={type} />
                      <span className="text-xs text-[var(--color-text-secondary)] tabular-nums">
                        {count}
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </section>
      )}

      {/* Top Co-Changes */}
      {coChanges && coChanges.co_changes.length > 0 && (
        <section>
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-medium flex items-center gap-2">
                  <GitMerge className="h-4 w-4 text-[var(--color-accent-primary)]" />
                  Top Cross-Repo Co-Changes
                </CardTitle>
                <Link
                  href="/workspace/co-changes"
                  className="text-xs text-[var(--color-accent-primary)] hover:underline flex items-center gap-1"
                >
                  View all <ArrowRight className="h-3 w-3" />
                </Link>
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <CoChangeTable coChanges={coChanges.co_changes} compact />
            </CardContent>
          </Card>
        </section>
      )}
    </div>
  );
}
