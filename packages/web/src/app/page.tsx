import type { Metadata } from "next";
import { redirect } from "next/navigation";
import Link from "next/link";
import {
  FileText,
  CheckCircle2,
  AlertCircle,
  Skull,
  Activity,
  RefreshCw,
  Clock,
} from "lucide-react";
import { listRepos, getRepoStats } from "@/lib/api/repos";
import { listJobs } from "@/lib/api/jobs";
import { getGitSummary } from "@/lib/api/git";
import { getWorkspace } from "@/lib/api/workspace";
import type { RepoStatsResponse, GitSummaryResponse } from "@/lib/api/types";
import { StatCard } from "@/components/shared/stat-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ConfidenceBadge } from "@/components/wiki/confidence-badge";
import { EmptyState } from "@/components/shared/empty-state";
import { formatRelativeTime, formatNumber } from "@/lib/utils/format";
import { scoreToStatus } from "@/lib/utils/confidence";

export const metadata: Metadata = { title: "Dashboard" };

export const revalidate = 30;

export default async function DashboardPage() {
  const [repos, jobs, ws] = await Promise.allSettled([
    listRepos(),
    listJobs({ limit: 10 }),
    getWorkspace(),
  ]);

  const repoList = repos.status === "fulfilled" ? repos.value : [];
  const jobList = jobs.status === "fulfilled" ? jobs.value : [];
  const workspace = ws.status === "fulfilled" ? ws.value : null;

  // Workspace mode → workspace dashboard
  if (workspace?.is_workspace) {
    redirect("/workspace");
  }

  // Single repo → go straight to its overview
  if (repoList.length === 1) {
    redirect(`/repos/${repoList[0].id}/overview`);
  }

  // Aggregate stats across all repos

  const statsResults = await Promise.allSettled(
    repoList.map((r) => getRepoStats(r.id)),
  );
  const gitResults = await Promise.allSettled(
    repoList.map((r) => getGitSummary(r.id)),
  );

  const statsMap = new Map<string, RepoStatsResponse>();
  const gitMap = new Map<string, GitSummaryResponse>();
  repoList.forEach((r, i) => {
    if (statsResults[i]?.status === "fulfilled")
      statsMap.set(r.id, (statsResults[i] as PromiseFulfilledResult<RepoStatsResponse>).value);
    if (gitResults[i]?.status === "fulfilled")
      gitMap.set(r.id, (gitResults[i] as PromiseFulfilledResult<GitSummaryResponse>).value);
  });

  // Aggregate stats across all repos
  let totalPages = 0;
  let freshPages = 0;
  let deadCode = 0;
  for (const s of statsMap.values()) {
    totalPages += s.file_count;
    freshPages += Math.round(s.file_count * s.doc_coverage_pct / 100);
    deadCode += s.dead_export_count;
  }
  const stalePages = totalPages - freshPages;

  return (
    <div className="p-5 sm:p-8 space-y-8 max-w-[1200px]">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">Dashboard</h1>
        <p className="text-sm text-[var(--color-text-secondary)] mt-1">
          {repoList.length} {repoList.length === 1 ? "repository" : "repositories"} registered
        </p>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Total Pages"
          value={formatNumber(totalPages)}
          icon={<FileText className="h-4 w-4" />}
        />
        <StatCard
          label="Fresh Pages"
          value={formatNumber(freshPages)}
          description="Confidence ≥ 80%"
          icon={<CheckCircle2 className="h-4 w-4 text-green-500" />}
        />
        <StatCard
          label="Stale Pages"
          value={formatNumber(stalePages)}
          description="Need regeneration"
          icon={<AlertCircle className="h-4 w-4 text-yellow-500" />}
        />
        <StatCard
          label="Dead Code"
          value={deadCode > 0 ? formatNumber(deadCode) : "—"}
          description={deadCode > 0 ? "Unused exports" : "Analyze to detect"}
          icon={<Skull className="h-4 w-4 text-[var(--color-text-tertiary)]" />}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Repositories */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Repositories</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {repoList.length === 0 ? (
              <div className="px-6 pb-6">
                <EmptyState
                  title="No repositories yet"
                  description="Run repowise init on a repository to get started."
                  icon={<FileText className="h-8 w-8" />}
                />
              </div>
            ) : (
              <ul className="divide-y divide-[var(--color-border-default)]">
                {repoList.map((repo) => (
                  <li key={repo.id}>
                    <Link
                      href={`/repos/${repo.id}`}
                      className="flex items-start gap-3 px-6 py-3.5 transition-colors hover:bg-[var(--color-bg-elevated)] group"
                    >
                      <div className="mt-0.5 h-2 w-2 rounded-full bg-[var(--color-accent-primary)] shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-medium text-[var(--color-text-primary)] truncate group-hover:text-[var(--color-accent-primary)] transition-colors">
                            {repo.name}
                          </p>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <p className="text-xs text-[var(--color-text-tertiary)] font-mono truncate" title={repo.local_path}>
                            {repo.local_path}
                          </p>
                        </div>
                        <div className="flex items-center gap-2 mt-1 flex-wrap">
                          {repo.head_commit && (
                            <span className="text-xs font-mono text-[var(--color-text-tertiary)]">
                              {repo.head_commit.slice(0, 7)}
                            </span>
                          )}
                          <span className="text-xs text-[var(--color-text-tertiary)]">
                            Updated {formatRelativeTime(repo.updated_at)}
                          </span>
                          {gitMap.has(repo.id) && (() => {
                            const g = gitMap.get(repo.id)!;
                            return (
                              <>
                                {g.hotspot_count > 0 && (
                                  <Badge variant="outdated">{g.hotspot_count} hotspot{g.hotspot_count !== 1 ? "s" : ""}</Badge>
                                )}
                                {g.stable_count > 0 && (
                                  <Badge variant="fresh">{g.stable_count} stable</Badge>
                                )}
                              </>
                            );
                          })()}
                        </div>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* Recent Jobs */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Recent Jobs</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {jobList.length === 0 ? (
              <div className="px-6 pb-6">
                <EmptyState
                  title="No jobs yet"
                  description="Jobs appear after running repowise init or sync."
                  icon={<Activity className="h-8 w-8" />}
                />
              </div>
            ) : (
              <ul className="divide-y divide-[var(--color-border-default)]">
                {jobList.map((job) => (
                  <li key={job.id}>
                    <Link
                      href={`/repos/${job.repository_id}/overview`}
                      className="flex items-center gap-3 px-6 py-3 hover:bg-[var(--color-bg-elevated)] transition-colors"
                    >
                    <JobStatusIcon status={job.status} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono text-[var(--color-text-secondary)]">
                          {job.status === "running" ? (
                            <span className="text-[var(--color-accent-primary)]">
                              {job.completed_pages}/{job.total_pages} pages
                            </span>
                          ) : (
                            `${job.total_pages} pages`
                          )}
                        </span>
                        <Badge
                          variant={
                            job.status === "completed"
                              ? "fresh"
                              : job.status === "failed"
                              ? "outdated"
                              : job.status === "running"
                              ? "accent"
                              : "default"
                          }
                        >
                          {job.status}
                        </Badge>
                      </div>
                      <p className="text-xs text-[var(--color-text-tertiary)] mt-0.5">
                        {job.model_name} · {formatRelativeTime(job.updated_at)}
                      </p>
                    </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function JobStatusIcon({ status }: { status: string }) {
  if (status === "running") {
    return (
      <RefreshCw className="h-4 w-4 shrink-0 animate-spin text-[var(--color-accent-primary)]" />
    );
  }
  if (status === "completed") {
    return <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" />;
  }
  if (status === "failed") {
    return <AlertCircle className="h-4 w-4 shrink-0 text-red-500" />;
  }
  return <Clock className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />;
}
