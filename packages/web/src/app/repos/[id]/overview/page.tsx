import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Hash } from "lucide-react";
import Link from "next/link";
import { getOverviewSummary } from "@/lib/api/overview";
import { getProviders } from "@/lib/api/providers";
import { getStatsHighlights } from "@/lib/api/stats";
import { StatsTeaserCard } from "@/components/overview/stats-teaser-card";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { PageShell } from "@repowise-dev/ui/shared";
import { FirstIndexExperience } from "@/components/repos/first-index-experience";
import { HealthOverviewCard } from "@repowise-dev/ui/dashboard/health-overview-card";
import { AttentionPanel } from "@repowise-dev/ui/dashboard/attention-panel";
import { KpiStrip, kpiDelta, type KpiItem } from "@repowise-dev/ui/dashboard/kpi-strip";
import { OverviewGrid } from "@repowise-dev/ui/dashboard/overview-grid";
import { SavingsMini } from "@repowise-dev/ui/dashboard/savings-mini";
import { LanguageDonut } from "@repowise-dev/ui/dashboard/language-donut";
import { KnowledgeGraphCard } from "@repowise-dev/ui/dashboard/explore-cards";
import { AskAnythingCardWrapper } from "@/components/overview/ask-anything-card-wrapper";
import { QuickActionsWrapper as QuickActions } from "@/components/dashboard/quick-actions-wrapper";
import { OverviewTabs } from "@/components/overview/overview-tabs";
import { ContributorsStripCard } from "@/components/overview/contributors-strip-card";
import { formatNumber, formatRelativeTime } from "@repowise-dev/ui/lib/format";

export const metadata: Metadata = { title: "Overview" };

interface Props {
  params: Promise<{ id: string }>;
}

async function safeFetch<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

export default async function OverviewPage({ params }: Props) {
  const { id } = await params;

  const [summary, providers, statsHighlights] = await Promise.all([
    safeFetch(() => getOverviewSummary(id)),
    safeFetch(() => getProviders()),
    safeFetch(() => getStatsHighlights(id)),
  ]);
  if (!summary) notFound();

  const { repo, stats, health, sync } = summary;
  const isFresh = stats.file_count === 0;

  const kpis: KpiItem[] = [
    {
      label: "Files",
      value: formatNumber(stats.file_count),
      href: `/repos/${id}/architecture?view=graph`,
      // File-count growth is neutral, not "good" — render the delta uncolored.
      delta: kpiDelta(stats.deltas.file_count, true),
    },
    {
      label: "Symbols",
      value: formatNumber(stats.symbol_count),
      href: `/repos/${id}/architecture?view=symbols`,
    },
    {
      label: "Doc Coverage",
      value: `${Math.round(stats.doc_coverage_pct)}%`,
      href: `/repos/${id}/docs/coverage`,
      gauge: stats.doc_coverage_pct,
    },
    {
      label: "Dead Exports",
      value: formatNumber(stats.dead_export_count),
      href: `/repos/${id}/code-health?tab=dead-code`,
    },
    {
      label: "Entry Points",
      value: formatNumber(stats.entry_point_count),
      href: `/repos/${id}/architecture?view=graph&viewMode=architecture`,
    },
  ];

  const langDistribution: Record<string, number> = {};
  for (const l of summary.languages) langDistribution[l.language] = l.file_count;

  const attentionItems = summary.attention.map((a) => ({
    id: a.id,
    type: a.type,
    title: a.title,
    description: a.description,
    severity: a.severity,
    target_id: a.target_id,
  }));

  const lastActivityAt = sync.last_sync_at ?? sync.last_resync_at ?? health.last_indexed_at;

  const headerMeta = (
    <div className="flex flex-wrap items-center gap-2">
      {repo.head_commit && (
        <Badge variant="outline" className="text-[10px] h-5 shrink-0">
          <Hash className="h-2.5 w-2.5" />
          {repo.head_commit.slice(0, 7)}
        </Badge>
      )}
      <Badge variant="outline" className="text-[10px] h-5 shrink-0">
        {repo.default_branch}
      </Badge>
      {lastActivityAt && (
        <span
          className="text-xs text-[var(--color-text-tertiary)]"
          title={new Date(lastActivityAt).toLocaleString()}
        >
          synced {formatRelativeTime(lastActivityAt)}
        </span>
      )}
    </div>
  );

  return (
    <PageShell title={repo.name} description={repo.local_path} actions={headerMeta} maxWidth="wide">
      {/* Fresh repos get one prominent index action instead of the sync
          toolbar; everything else appears once the first index lands. */}
      {!isFresh && (
        <QuickActions
          repoId={id}
          repoName={repo.name}
          pageCount={sync.page_count || stats.file_count}
          modelName={providers?.active.model ?? sync.last_sync_model ?? ""}
          lastSyncAt={sync.last_sync_at}
          lastResyncAt={sync.last_resync_at}
        />
      )}

      {isFresh ? (
        <FirstIndexExperience repoId={id} repoName={repo.name} />
      ) : (
        <>
          {/* ── KPI bar leads ── */}
          <KpiStrip items={kpis} LinkComponent={Link} />

          {/* ── Code Health (our moat) leads, with triage + savings rail ── */}
          <OverviewGrid
            main={
              <>
                <HealthOverviewCard
                  data={health}
                  repoId={id}
                  averageDelta={stats.deltas.average_health}
                  hotspotDelta={stats.deltas.hotspot_health}
                />
                <ContributorsStripCard repoId={id} />
              </>
            }
            rail={
              <>
                {statsHighlights && <StatsTeaserCard repoId={id} data={statsHighlights} />}
                <SavingsMini data={summary.savings} repoId={id} />
                <AttentionPanel items={attentionItems} repoId={id} previewCount={5} repoName={repo.name} />
              </>
            }
          />

          {/* ── Pulse / Structure ── */}
          <OverviewTabs
            repoId={id}
            hotspots={summary.top_hotspots}
            hotspotTotal={stats.hotspot_count}
            decisions={summary.recent_decisions}
          />

          {/* ── Languages + explore: donut alongside the graph and chat front-doors ── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            {summary.languages.length > 0 && (
              <LanguageDonut
                distribution={langDistribution}
                viewAllHref={`/repos/${id}/architecture?view=graph&colorMode=language`}
              />
            )}
            <KnowledgeGraphCard href={`/repos/${id}/knowledge-graph`} />
            <AskAnythingCardWrapper repoId={id} />
          </div>
        </>
      )}
    </PageShell>
  );
}
