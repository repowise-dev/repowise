import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Hash } from "lucide-react";
import { getOverviewSummary } from "@/lib/api/overview";
import { getProviders } from "@/lib/api/providers";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { Card } from "@repowise-dev/ui/ui/card";
import { EmptyState } from "@repowise-dev/ui/shared";
import { HealthScoreBadge } from "@repowise-dev/ui/dashboard/health-score-badge";
import { HealthOverviewCard } from "@repowise-dev/ui/dashboard/health-overview-card";
import { WhereToStartCard } from "@repowise-dev/ui/dashboard/where-to-start";
import { SavingsMini } from "@repowise-dev/ui/dashboard/savings-mini";
import { LanguageDonut } from "@repowise-dev/ui/dashboard/language-donut";
import { QuickActionsWrapper as QuickActions } from "@/components/dashboard/quick-actions-wrapper";
import { OverviewTabs } from "@/components/overview/overview-tabs";
import { ContributorsStripCard } from "@/components/overview/contributors-strip-card";
import { computeHealthScore } from "@/lib/utils/health-score";
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

interface Kpi {
  label: string;
  value: string;
  href: string;
  delta?: { value: string; positive: boolean };
}

function kpiDelta(delta: number | null | undefined): Kpi["delta"] {
  if (delta == null || delta === 0) return undefined;
  return { value: `${delta > 0 ? "+" : ""}${delta}`, positive: delta > 0 };
}

/** Aligned enterprise-style KPI bar — one card, evenly divided cells. */
function KpiStrip({ items }: { items: Kpi[] }) {
  return (
    <Card className="overflow-hidden shadow-sm">
      <div className="grid grid-cols-2 divide-x divide-y divide-[var(--color-border-default)] sm:grid-cols-3 sm:divide-y-0 lg:grid-cols-5">
        {items.map((kpi) => (
          <a
            key={kpi.label}
            href={kpi.href}
            className="group flex flex-col gap-1 px-4 py-3.5 transition-colors hover:bg-[var(--color-bg-elevated)]"
          >
            <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
              {kpi.label}
            </span>
            <span className="flex items-baseline gap-1.5">
              <span className="text-2xl font-bold tabular-nums leading-none text-[var(--color-text-primary)] group-hover:text-[var(--color-accent-primary)] transition-colors">
                {kpi.value}
              </span>
              {kpi.delta && (
                <span
                  className="text-xs font-medium tabular-nums"
                  style={{
                    color: kpi.delta.positive
                      ? "var(--color-success)"
                      : "var(--color-error)",
                  }}
                >
                  {kpi.delta.positive ? "↑" : "↓"} {kpi.delta.value}
                </span>
              )}
            </span>
          </a>
        ))}
      </div>
    </Card>
  );
}

export default async function OverviewPage({ params }: Props) {
  const { id } = await params;

  const [summary, providers] = await Promise.all([
    safeFetch(() => getOverviewSummary(id)),
    safeFetch(() => getProviders()),
  ]);
  if (!summary) notFound();

  const { repo, stats, health, sync } = summary;
  const isFresh = stats.file_count === 0;

  const healthScore = computeHealthScore({
    docCoveragePct: stats.doc_coverage_pct,
    freshnessScore: stats.freshness_score,
    deadExportCount: stats.dead_export_count,
    symbolCount: stats.symbol_count || 1,
    hotspotCount: stats.hotspot_count,
    totalFiles: stats.file_count || 1,
    siloCount: stats.silo_count,
    totalModules: stats.module_count || 1,
  });

  const kpis: Kpi[] = [
    {
      label: "Files",
      value: formatNumber(stats.file_count),
      href: `/repos/${id}/architecture?view=graph`,
      delta: kpiDelta(stats.deltas.file_count),
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
    },
    {
      label: "Dead Exports",
      value: formatNumber(stats.dead_export_count),
      href: `/repos/${id}/code-health?tab=dead-code`,
    },
    {
      label: "Hotspots",
      value: formatNumber(stats.hotspot_count),
      href: `/repos/${id}/code-health?tab=hotspots`,
    },
  ];

  const langDistribution: Record<string, number> = {};
  for (const l of summary.languages) langDistribution[l.language] = l.file_count;

  const lastActivityAt = sync.last_sync_at ?? sync.last_resync_at ?? health.last_indexed_at;

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      {/* ── Compact header ── */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
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
          {!isFresh && (
            <HealthScoreBadge
              score={healthScore.score}
              components={healthScore.components}
              note={healthScore.note}
              history={health.history}
            />
          )}
          {lastActivityAt && (
            <span
              className="text-[11px] text-[var(--color-text-tertiary)]"
              title={new Date(lastActivityAt).toLocaleString()}
            >
              synced {formatRelativeTime(lastActivityAt)}
            </span>
          )}
        </div>
        <p className="text-xs font-mono text-[var(--color-text-tertiary)] truncate -mt-1">
          {repo.local_path}
        </p>
        <QuickActions
          repoId={id}
          repoName={repo.name}
          pageCount={sync.page_count || stats.file_count}
          modelName={providers?.active.model ?? sync.last_sync_model ?? ""}
          lastSyncAt={sync.last_sync_at}
          lastResyncAt={sync.last_resync_at}
        />
      </div>

      {isFresh ? (
        <EmptyState
          title={
            lastActivityAt
              ? `Indexed ${formatRelativeTime(lastActivityAt)} — nothing to show yet`
              : "This repo hasn't been indexed yet"
          }
          description="Run a sync (above) or `repowise init` in the repo to populate the overview. Stats, attention items, and activity appear as soon as the first index lands."
        />
      ) : (
        <>
          {/* ── KPI bar leads ── */}
          <KpiStrip items={kpis} />

          {/* ── Code Health (our moat) leads, with onboarding + savings rail ── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="space-y-4 lg:col-span-2">
              <HealthOverviewCard
                data={health}
                repoId={id}
                averageDelta={stats.deltas.average_health}
                hotspotDelta={stats.deltas.hotspot_health}
              />
              <ContributorsStripCard repoId={id} />
            </div>
            <div className="space-y-4">
              <WhereToStartCard targets={summary.onboarding_targets} repoId={id} />
              <SavingsMini data={summary.savings} repoId={id} />
            </div>
          </div>

          {/* ── Pulse / Structure ── */}
          <OverviewTabs
            repoId={id}
            hotspots={summary.top_hotspots}
            hotspotTotal={stats.hotspot_count}
            decisions={summary.recent_decisions}
          />

          {/* ── Languages (moved out of the savings card) ── */}
          {summary.languages.length > 0 && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <LanguageDonut
                distribution={langDistribution}
                viewAllHref={`/repos/${id}/architecture?view=graph&colorMode=language`}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
