/**
 * Health dashboard: a KPI header over the three health signals, the circle-pack
 * code map as the hero, and the churn-versus-complexity quadrant on a tab. Every
 * file click opens the file in an editor column via the host.
 */

import { useMemo, useState } from "react";
import { HealthKpiCards } from "@repowise-dev/ui/health/kpi-cards";
import { CodeHealthMap, type CodeHealthOverlay } from "@repowise-dev/ui/health/code-health-map";
import { ChurnComplexityQuadrant } from "@repowise-dev/ui/health/churn-complexity-quadrant";
import type { HealthDimension } from "@repowise-dev/types/health";
import type { ViewProps } from "../../runtime/mount";
import { useDashboardData, type DashboardData } from "./useDashboardData";
import { DashboardError, DashboardSkeleton, SectionHeading } from "./chrome";

type HeroTab = "map" | "quadrant";

/** The KPI pillar tiles map onto the code map's lenses (defect is "health"). */
const PILLAR_TO_OVERLAY: Record<HealthDimension, CodeHealthOverlay> = {
  defect: "health",
  maintainability: "maintainability",
  performance: "performance",
};

export function App({ host, repo, refreshToken }: ViewProps<"health">) {
  const { data, error, loading } = useDashboardData(host, refreshToken);

  if (error) {
    return <DashboardError message={error} />;
  }
  if (loading || !data) {
    return <DashboardSkeleton />;
  }

  return <Dashboard host={host} repo={repo} data={data} />;
}

function Dashboard({
  host,
  repo,
  data,
}: {
  host: ViewProps<"health">["host"];
  repo: ViewProps<"health">["repo"];
  data: DashboardData;
}) {
  const { overview, files, trend, churn } = data;
  const [overlay, setOverlay] = useState<CodeHealthOverlay>("health");
  const [tab, setTab] = useState<HeroTab>("map");

  // KPI sparklines want oldest-first series; the trend history is newest-first.
  const kpiTrend = useMemo(() => {
    const history = trend.history ?? [];
    const asc = history.slice().reverse();
    const out: {
      averageHistory?: number[];
      hotspotHistory?: number[];
      worstHistory?: number[];
      averageDelta?: number;
      hotspotDelta?: number;
    } = {
      averageHistory: asc.map((p) => p.average_health),
      hotspotHistory: asc.map((p) => p.hotspot_health),
      worstHistory: asc.map((p) => p.worst_performer_score ?? 0),
    };
    if (trend.summary?.average_delta != null) out.averageDelta = trend.summary.average_delta;
    if (trend.summary?.hotspot_delta != null) out.hotspotDelta = trend.summary.hotspot_delta;
    return out;
  }, [trend]);

  const headCommit = repo.headCommit ? repo.headCommit.slice(0, 7) : null;

  return (
    <div className="mx-auto max-w-[1400px] space-y-8 px-6 py-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight text-[var(--color-text-primary)]">
          Code health
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          {repo.name}
          {headCommit ? (
            <span className="ml-2 font-mono text-xs text-[var(--color-text-tertiary)]">
              {headCommit}
            </span>
          ) : null}
        </p>
      </header>

      <section aria-label="Health signals">
        <HealthKpiCards
          summary={overview.summary}
          distribution={overview.distribution ?? null}
          {...kpiTrend}
          onSelectPillar={(pillar) => {
            setOverlay(PILLAR_TO_OVERLAY[pillar]);
            setTab("map");
          }}
        />
      </section>

      <section aria-label="Health explorer" className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <SectionHeading
            title={tab === "map" ? "Code health map" : "Churn versus complexity"}
            subtitle={
              tab === "map"
                ? `${files.total.toLocaleString()} files · click a galaxy to zoom, a file to open`
                : `${churn.total.toLocaleString()} changed files · the top-right corner is the refactor zone`
            }
          />
          <TabSwitch tab={tab} onChange={setTab} />
        </div>

        {tab === "map" ? (
          <CodeHealthMap
            files={files.files}
            overlay={overlay}
            onOverlayChange={setOverlay}
            onSelectFile={(path) => host.openFile(path)}
            minHeight={640}
          />
        ) : (
          <ChurnComplexityQuadrant
            points={churn.points}
            height={520}
            onSelect={(point) => host.openFile(point.file_path)}
          />
        )}
      </section>
    </div>
  );
}

/** A two-button segmented control switching the hero between map and quadrant. */
function TabSwitch({ tab, onChange }: { tab: HeroTab; onChange: (tab: HeroTab) => void }) {
  const tabs: { id: HeroTab; label: string }[] = [
    { id: "map", label: "Map" },
    { id: "quadrant", label: "Quadrant" },
  ];
  return (
    <div className="inline-flex shrink-0 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-0.5 text-xs">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          aria-pressed={tab === t.id}
          className={
            tab === t.id
              ? "rounded-md bg-[var(--color-accent-primary)] px-3 py-1 font-medium text-[var(--color-text-inverse)]"
              : "rounded-md px-3 py-1 text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)]"
          }
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
