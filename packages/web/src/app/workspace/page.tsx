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
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { RepoCard } from "@repowise-dev/ui/workspace/repo-card";
import { CrossRepoSummary } from "@repowise-dev/ui/workspace/cross-repo-summary";
import { CoChangeTable } from "@repowise-dev/ui/workspace/co-change-table";
import { ContractTypeBadge } from "@repowise-dev/ui/workspace/contract-type-badge";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { WorkspaceGraphSection } from "./workspace-graph-section";
import { SyncButton } from "./sync-buttons";

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

  // "Needs attention" rollup across the whole workspace (not a page slice).
  const needsIndex = wsRepos.filter(
    (r) => (r.status ?? (r.repo_id ? "indexed" : "needs_index")) === "needs_index",
  );
  const missingDirs = wsRepos.filter((r) => r.status === "missing_dir");
  const docsSkipped = wsRepos.filter((r) => r.docs_skip_reason);
  const attentionItems: string[] = [];
  if (needsIndex.length > 0)
    attentionItems.push(
      `${needsIndex.length} repo${needsIndex.length === 1 ? "" : "s"} not indexed yet (${needsIndex
        .slice(0, 3)
        .map((r) => r.alias)
        .join(", ")}${needsIndex.length > 3 ? ", …" : ""})`,
    );
  if (missingDirs.length > 0)
    attentionItems.push(
      `${missingDirs.length} repo director${missingDirs.length === 1 ? "y is" : "ies are"} missing on disk (${missingDirs
        .slice(0, 3)
        .map((r) => r.alias)
        .join(", ")}${missingDirs.length > 3 ? ", …" : ""})`,
    );
  if (docsSkipped.length > 0)
    attentionItems.push(
      `docs skipped for ${docsSkipped.length} repo${docsSkipped.length === 1 ? "" : "s"}`,
    );

  return (
    <div className="p-5 sm:p-8 space-y-8 max-w-[1200px]">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
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
              <span
                className="text-[var(--color-text-tertiary)] font-mono break-all"
                title={workspace.workspace_root}
              >
                {" "}&middot; {workspace.workspace_root}
              </span>
            )}
          </p>
        </div>
        {(workspace?.repos.length ?? 0) > 0 && (
          <SyncButton variant="primary" label="Sync workspace" />
        )}
      </div>

      {/* Needs attention */}
      {attentionItems.length > 0 && (
        <div className="rounded-lg border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/5 px-4 py-3">
          <p className="text-xs font-medium uppercase tracking-wider text-[var(--color-warning)]">
            Needs attention
          </p>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-xs text-[var(--color-text-secondary)]">
            {attentionItems.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

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
          icon={<Flame className="h-4 w-4 text-[var(--color-warning)]" />}
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
        {wsRepos.length === 0 && (
          <EmptyState
            title="No repositories discovered yet"
            description="Run `repowise init .` in the workspace root to scan for git repositories and index them. They show up here as soon as the scan lands."
            icon={<Layers className="h-8 w-8" />}
          />
        )}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {wsRepos.map((wsRepo) => {
            const status = wsRepo.status ?? (wsRepo.repo_id ? "indexed" : "needs_index");
            const isUnindexed = status !== "indexed";
            return (
              <RepoCard
                key={wsRepo.alias}
                repoId={wsRepo.repo_id ?? ""}
                alias={wsRepo.alias}
                name={wsRepo.alias}
                path={wsRepo.path}
                isPrimary={wsRepo.is_primary}
                status={status}
                docsSkipReason={wsRepo.docs_skip_reason ?? null}
                stats={wsRepo.file_count > 0 ? {
                  file_count: wsRepo.file_count,
                  doc_coverage_pct: wsRepo.doc_coverage_pct,
                } : null}
                gitSummary={wsRepo.file_count > 0 ? {
                  hotspot_count: wsRepo.hotspot_count,
                } : null}
                actions={
                  status !== "missing_dir" ? (
                    <SyncButton
                      alias={wsRepo.alias}
                      label={isUnindexed ? "Index now" : "Sync"}
                    />
                  ) : null
                }
              />
            );
          })}
        </div>
      </section>

      {/* Cross-Repo Graph */}
      <WorkspaceGraphSection repoCount={wsRepos.length} />

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
