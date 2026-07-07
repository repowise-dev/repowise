/**
 * Health dashboard: a KPI header over the three health signals, the circle-pack
 * code map as the hero, and the churn-versus-complexity quadrant on a tab. Every
 * file click opens the file in an editor column via the host.
 */

import { useMemo, useState } from "react";
import { ExternalLink } from "lucide-react";
import { HealthKpiCards } from "@repowise-dev/ui/health/kpi-cards";
import { CodeHealthMap, type CodeHealthOverlay } from "@repowise-dev/ui/health/code-health-map";
import { ChurnComplexityQuadrant } from "@repowise-dev/ui/health/churn-complexity-quadrant";
import type { HealthDimension, HealthFileMetric } from "@repowise-dev/types/health";
import type { ViewProps } from "../../runtime/mount";
import { useDashboardData, type DashboardData } from "./useDashboardData";
import { DashboardError, DashboardSkeleton, SectionHeading } from "./chrome";

type HeroTab = "map" | "quadrant";

/** 0-10 health score to its band color; matches the sidebar Home hero. */
function scoreColor(score: number | null): string {
  if (score == null) return "var(--color-text-tertiary)";
  if (score >= 7.5) return "var(--color-success)";
  if (score >= 5) return "var(--color-warning)";
  return "var(--color-error)";
}

/** The KPI pillar tiles map onto the code map's lenses (defect is "health"). */
const PILLAR_TO_OVERLAY: Record<HealthDimension, CodeHealthOverlay> = {
  defect: "health",
  maintainability: "maintainability",
  performance: "performance",
};

export function App({ host, repo, params, refreshToken }: ViewProps<"health">) {
  const { data, error, loading } = useDashboardData(host, refreshToken);

  if (error) {
    return <DashboardError message={error} />;
  }
  if (loading || !data) {
    return <DashboardSkeleton />;
  }

  return <Dashboard host={host} repo={repo} data={data} selectPath={params.selectPath ?? null} />;
}

function Dashboard({
  host,
  repo,
  data,
  selectPath,
}: {
  host: ViewProps<"health">["host"];
  repo: ViewProps<"health">["repo"];
  data: DashboardData;
  selectPath: string | null;
}) {
  const { overview, files, trend, churn } = data;

  // When the dashboard is opened from the status-bar score, lead with a card
  // for that file so the two surfaces read as one. The map file set is large
  // (nloc desc), so the active file is almost always present; if not, the card
  // still offers a way to open it.
  const focused = useMemo(() => {
    if (!selectPath) return null;
    const entry = files.files.find((f) => f.file_path === selectPath) ?? null;
    return { path: selectPath, entry };
  }, [selectPath, files.files]);
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

      {focused ? (
        <FocusedFile
          path={focused.path}
          entry={focused.entry}
          onOpen={() => host.openFile(focused.path)}
        />
      ) : null}

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

/** The file the dashboard was opened for (from the status-bar score), pinned
 *  above the repo-wide views with its three scores and a jump-to-file action. */
function FocusedFile({
  path,
  entry,
  onOpen,
}: {
  path: string;
  entry: HealthFileMetric | null;
  onOpen: () => void;
}) {
  const name = path.split("/").pop() ?? path;
  return (
    <section
      aria-label="Current file"
      className="flex items-center justify-between gap-4 rounded-xl border border-[var(--color-accent-muted)] bg-[var(--color-bg-surface)] px-4 py-3"
    >
      <div className="min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
          Current file
        </p>
        <button
          type="button"
          onClick={onOpen}
          className="flex max-w-full items-center gap-1.5 truncate font-mono text-sm text-[var(--color-text-primary)] transition-colors hover:text-[var(--color-accent-primary)]"
          title={path}
        >
          <span className="truncate">{name}</span>
          <ExternalLink className="h-3 w-3 shrink-0 opacity-70" />
        </button>
      </div>
      {entry ? (
        <div className="flex shrink-0 items-center gap-4 text-right">
          <FocusedScore label="Defect" value={entry.defect_score ?? entry.score} />
          <FocusedScore label="Maint" value={entry.maintainability_score ?? null} />
          <FocusedScore label="Perf" value={entry.performance_score ?? null} />
        </div>
      ) : (
        <span className="shrink-0 text-[11px] text-[var(--color-text-tertiary)]">
          Not among the mapped files
        </span>
      )}
    </section>
  );
}

function FocusedScore({ label, value }: { label: string; value: number | null }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">{label}</p>
      <p className="font-mono text-sm tabular-nums" style={{ color: scoreColor(value) }}>
        {value == null ? "-" : value.toFixed(1)}
      </p>
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
