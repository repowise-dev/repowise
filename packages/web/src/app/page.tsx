import type { Metadata } from "next";
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
import { listRepos } from "@/lib/api/repos";
import { listJobs } from "@/lib/api/jobs";
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
  const [repos, jobs] = await Promise.allSettled([
    listRepos(),
    listJobs({ limit: 10 }),
  ]);

  const repoList = repos.status === "fulfilled" ? repos.value : [];
  const jobList = jobs.status === "fulfilled" ? jobs.value : [];

  // Aggregate stats across all repos
  const totalPages = 0; // fetched per repo in future
  const freshPages = 0;
  const stalePages = 0;

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1200px]">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">Dashboard</h1>
        <p className="text-sm text-[var(--color-text-secondary)] mt-0.5">
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
          value="—"
          description="Analyze to detect"
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
                          <p className="text-xs text-[var(--color-text-tertiary)] font-mono truncate">
                            {repo.local_path}
                          </p>
                        </div>
                        <div className="flex items-center gap-2 mt-1">
                          {repo.head_commit && (
                            <span className="text-xs font-mono text-[var(--color-text-tertiary)]">
                              {repo.head_commit.slice(0, 7)}
                            </span>
                          )}
                          <span className="text-xs text-[var(--color-text-tertiary)]">
                            Updated {formatRelativeTime(repo.updated_at)}
                          </span>
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
                  <li
                    key={job.id}
                    className="flex items-center gap-3 px-6 py-3"
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
