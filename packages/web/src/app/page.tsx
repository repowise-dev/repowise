import type { Metadata } from "next";
import { redirect } from "next/navigation";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { LayoutGrid } from "lucide-react";
import type { RepoSummaryRow } from "@repowise-dev/types/repos";
import { PageShell } from "@repowise-dev/ui/shared";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { OverviewSection, RepoRows, type RepoRow } from "@repowise-dev/ui/overview";
// Direct path, not the `ui/stats` barrel: that barrel re-exports two
// "use client" modules, which would drag a hydration boundary into this
// server page for a component that has no state.
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { getReposSummary } from "@/lib/api/repos";
import { listJobs } from "@/lib/api/jobs";
import { getWorkspace } from "@/lib/api/workspace";
import { attentionSentence, byAttention } from "@/lib/repo-attention";
import { DeleteRepoButton } from "@/components/repos/delete-repo-button";
import { EmptyReposState } from "@/components/repos/empty-repos-state";
import { JobRows } from "@/components/jobs/job-rows";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("dashboard");
  return { title: t("title") };
}

export const revalidate = 30;

/** Jobs the activity section holds. One screen's worth; the figure is not
 *  reported anywhere, so this cap cannot be mistaken for a total. */
const JOB_WINDOW = 10;

/**
 * The multi-repo dashboard.
 *
 * Only ever rendered with two or more repositories: one redirects to its
 * overview and workspace mode redirects to `/workspace`, both below.
 *
 * It used to open with four cross-repo totals in `MetricCard`s — total pages,
 * fresh, stale, dead code — and put the repositories themselves in a card
 * underneath. Two things were wrong with that beyond the styling. Summing
 * findings across unrelated repositories produces a number with no action
 * attached to it, and the headline figure was `file_count` from `/stats`,
 * which counted symbol nodes as files and so over-reported by roughly 10x
 * under a label that said "Total Pages". Both are fixed at the source; see
 * `GET /api/repos/summary`.
 *
 * So the list is the subject now, ordered by what needs attention rather than
 * by last write, and the aggregates that do legitimately add sit in the ribbon
 * above it.
 */
export default async function DashboardPage() {
  const t = await getTranslations("dashboard");
  const ta = await getTranslations("attention");
  // One wave. The shape this replaces fetched the repo list, then a stats call
  // per repo, then a git-summary call per repo, in two sequential rounds.
  const [summary, jobs, ws] = await Promise.allSettled([
    getReposSummary(),
    listJobs({ limit: JOB_WINDOW }),
    getWorkspace({ cache: "no-store" }),
  ]);

  const repos = summary.status === "fulfilled" ? summary.value.repos : [];
  const jobList = jobs.status === "fulfilled" ? jobs.value : [];
  const workspace = ws.status === "fulfilled" ? ws.value : null;

  if (workspace?.is_workspace) redirect("/workspace");
  if (repos.length === 1) redirect(`/repos/${repos[0].id}/overview`);

  if (repos.length === 0) {
    return (
      <PageShell
        title={t("title")}
        icon={<LayoutGrid className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
        description={t("description")}
      >
        <EmptyReposState />
      </PageShell>
    );
  }

  const totals = repos.reduce(
    (acc, r) => ({
      files: acc.files + r.file_count,
      symbols: acc.symbols + r.symbol_count,
      pages: acc.pages + r.doc_page_count,
      freshPages: acc.freshPages + r.doc_fresh_page_count,
      hotspots: acc.hotspots + r.hotspot_count,
      deadExports: acc.deadExports + r.dead_export_count,
    }),
    { files: 0, symbols: 0, pages: 0, freshPages: 0, hotspots: 0, deadExports: 0 },
  );

  const ribbon: RibbonStat[] = [
    {
      label: t("ribbon.files"),
      value: formatNumber(totals.files),
      sub: t("ribbon.filesSub", { count: repos.length }),
    },
    {
      label: t("ribbon.symbols"),
      value: formatNumber(totals.symbols),
      sub: t("ribbon.symbolsSub"),
    },
    {
      label: t("ribbon.docFreshness"),
      value: totals.pages > 0 ? `${Math.round((totals.freshPages / totals.pages) * 100)}%` : "—",
      sub:
        totals.pages > 0
          ? t("ribbon.docFreshnessSub", {
              fresh: formatNumber(totals.freshPages),
              total: formatNumber(totals.pages),
            })
          : t("ribbon.docFreshnessNone"),
    },
    {
      label: t("ribbon.hotspots"),
      value: formatNumber(totals.hotspots),
      sub: t("ribbon.hotspotsSub"),
    },
    {
      label: t("ribbon.unusedExports"),
      value: formatNumber(totals.deadExports),
      sub: t("ribbon.unusedExportsSub"),
    },
  ];

  const activeJobs = jobList.filter((j) => j.status === "running" || j.status === "pending");
  const nameFor = (id: string) => repos.find((r) => r.id === id)?.name ?? null;

  return (
    <PageShell
      title={t("title")}
      icon={<LayoutGrid className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
      description={t("description")}
    >
      <PageLede
        label={t("ledeLabel")}
        value={String(repos.length)}
        unit={t("ledeUnit")}
        layout="beside"
      >
        <p>
          {t("intro", {
            files: formatNumber(totals.files),
            symbols: formatNumber(totals.symbols),
            pages: formatNumber(totals.pages),
          })}
        </p>
        <p>{attentionSentence(repos, ta)}</p>
      </PageLede>

      <StatRibbon stats={ribbon} />

      {activeJobs.length > 0 && (
        <OverviewSection
          title={
            activeJobs.length === 1
              ? t("indexingNow")
              : t("indexingNowCount", { count: activeJobs.length })
          }
          description={t("indexingNowDescription")}
        >
          <JobRows jobs={activeJobs} nameFor={nameFor} />
        </OverviewSection>
      )}

      <OverviewSection
        title={t("repositoriesTitle")}
        description={t("repositoriesDescription")}
      >
        <RepoRows
          repos={repos.slice().sort(byAttention).map(toRow)}
          LinkComponent={Link}
          actionsFor={(repo) => <DeleteRepoButton repoId={repo.id} repoName={repo.name} />}
        />
      </OverviewSection>

      {jobList.length > 0 && (
        <OverviewSection
          title={t("recentActivity")}
          description={t("recentActivityDescription")}
        >
          <JobRows jobs={jobList} nameFor={nameFor} />
        </OverviewSection>
      )}
    </PageShell>
  );
}

function toRow(repo: RepoSummaryRow): RepoRow {
  return {
    id: repo.id,
    name: repo.name,
    localPath: repo.local_path,
    href: `/repos/${repo.id}/overview`,
    status: repo.status,
    health: repo.average_health,
    fileCount: repo.file_count,
    hotspotCount: repo.hotspot_count,
    docPageCount: repo.doc_page_count,
    docFreshPageCount: repo.doc_fresh_page_count,
    deadExportCount: repo.dead_export_count,
    updatedAt: repo.updated_at,
    indexBehind: repo.index_behind,
  };
}
