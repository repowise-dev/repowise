/**
 * Health dashboard: the shared code-health lede, the circle-pack map as the
 * page spine with its lens switcher and key around it, and the trend section
 * under it. Every file click opens the file in an editor column via the host.
 *
 * The composition mirrors `/repos/[id]/code-health` rather than reimplementing
 * it. That page dropped its KPI grid for `CodeHealthLede` and folded the
 * churn-versus-complexity quadrant into a *lens* on the map; this panel was the
 * last consumer of both retired components in the monorepo, which is how it was
 * going to diverge permanently. It composes the same pieces rather than
 * mounting `TriageView` wholesale, because the drill-downs that view carries
 * (findings tables, coverage, a file drawer, href-based navigation) are
 * surfaces this panel does not have and an editor cannot address by URL.
 */

import { useMemo, useState } from "react";
import { ExternalLink } from "lucide-react";
import {
  CodeHealthMap,
  MapLegend,
  MapLensSwitcher,
  type CodeHealthOverlay,
} from "@repowise-dev/ui/health/code-health-map";
import { CodeHealthLede } from "@repowise-dev/ui/health/code-health-lede";
import { TrendView } from "@repowise-dev/ui/health/trend-view";
import { scoreTextColor } from "@repowise-dev/ui/health/tokens";
import { OverviewSection } from "@repowise-dev/ui/overview";
import type { HealthFileMetric } from "@repowise-dev/types/health";
import type { ViewProps } from "../../runtime/mount";
import {
  useChurnLens,
  useDashboardData,
  type DashboardData,
} from "./useDashboardData";
import { DashboardError, DashboardSkeleton } from "./chrome";

/**
 * Lenses offered. The three co-equal health signals ride on the map payload
 * itself; churn arrives on its own request and is joined in below, which is why
 * it is listed here rather than left to the map component's default.
 */
const LENSES: CodeHealthOverlay[] = ["health", "maintainability", "performance", "churn"];

const MAP_HEIGHT = 640;

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
  const { overview, files, trend } = data;
  const [overlay, setOverlay] = useState<CodeHealthOverlay>("health");

  // Churn is a second request and only the churn lens colors from it, so it is
  // fetched on selection rather than on mount.
  const {
    files: mapFiles,
    loading: churnLoading,
    failed: churnFailed,
  } = useChurnLens(host, files, overlay === "churn");

  // When the dashboard is opened from the status-bar score, lead with a row
  // for that file so the two surfaces read as one. The map file set is large
  // (nloc desc), so the active file is almost always present; if not, the row
  // still offers a way to open it.
  const focused = useMemo(() => {
    if (!selectPath) return null;
    const entry = files.files.find((f) => f.file_path === selectPath) ?? null;
    return { path: selectPath, entry };
  }, [selectPath, files.files]);

  const headCommit = repo.headCommit ? repo.headCommit.slice(0, 7) : null;

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 px-6 py-6 sm:gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-[22px] font-semibold tracking-tight text-[var(--color-text-primary)]">
          Code health
        </h1>
        <p className="text-[15px] text-[var(--color-text-secondary)]">
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

      <CodeHealthLede
        summary={overview.summary}
        accuracy={overview.defect_accuracy ?? null}
        distribution={overview.distribution ?? null}
      />

      <OverviewSection
        title="Code health map"
        description="Every file as a node, clustered into module galaxies and sized by lines of code. The lens recolors the same field rather than redrawing it. Click a galaxy to zoom, a file to open it."
        action={
          <MapLensSwitcher overlay={overlay} onOverlayChange={setOverlay} lenses={LENSES} />
        }
      >
        <div className="flex flex-col gap-2.5">
          <CodeHealthMap
            files={mapFiles.files}
            overlay={overlay}
            onSelectFile={(path) => host.openFile(path)}
            minHeight={MAP_HEIGHT}
            chrome="none"
            scope={{
              shown: mapFiles.shown,
              eligible: mapFiles.eligible_total,
              repository: mapFiles.repository_total,
              cap: mapFiles.cap,
              omitted: {
                files: mapFiles.omitted.files,
                performanceFiles: mapFiles.omitted.performance_files,
                opportunities: mapFiles.omitted.opportunities,
                observations: mapFiles.omitted.observations,
              },
            }}
          />
          <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
            {/* A key describing bands the field cannot be showing is worse
                than no key: every node is the "no data" swatch when the churn
                request failed, so say that instead. */}
            {churnFailed ? (
              <span className="text-[11px] text-[var(--color-text-tertiary)]">
                Could not load churn for this lens. The other lenses are
                unaffected.
              </span>
            ) : (
              <MapLegend overlay={overlay} loading={churnLoading} />
            )}
          </div>
        </div>
      </OverviewSection>

      <OverviewSection
        title="Trend"
        description="How the repo's scores have moved across indexed snapshots."
      >
        <TrendView data={trend} isLoading={false} error={null} />
      </OverviewSection>
    </div>
  );
}

/** The file the dashboard was opened for (from the status-bar score), pinned
 *  above the repo-wide views with its three scores and a jump-to-file action.
 *  A hairline row rather than a card: the file name is the only thing here you
 *  can act on, and the three scores beside it are statistics. */
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
      className="flex items-center justify-between gap-4 border-t border-[var(--color-border-default)] pt-4"
    >
      <div className="min-w-0">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          Current file
        </p>
        <button
          type="button"
          onClick={onOpen}
          className="mt-0.5 flex max-w-full items-center gap-1.5 font-mono text-[15px] text-[var(--color-text-primary)] transition-colors hover:text-[var(--color-accent-primary)]"
          title={path}
        >
          <span className="truncate">{name}</span>
          <ExternalLink className="h-3 w-3 shrink-0 opacity-70" />
        </button>
      </div>
      {entry ? (
        <div className="flex shrink-0 items-center gap-5 text-right">
          <FocusedScore label="Defect" value={entry.defect_score ?? entry.score} />
          <FocusedScore label="Maint" value={entry.maintainability_score ?? null} />
          <FocusedScore label="Perf" value={entry.performance_score ?? null} />
        </div>
      ) : (
        <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
          Not among the mapped files
        </span>
      )}
    </section>
  );
}

function FocusedScore({ label, value }: { label: string; value: number | null }) {
  // The shared ramp, so this figure agrees with the map beside it and with the
  // web app's file table. There is no local threshold here on purpose: the one
  // this file used to carry called 7.6 healthy while the map coloured it amber.
  const tone = value == null ? "text-[var(--color-text-tertiary)]" : scoreTextColor(value);
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {label}
      </p>
      <p className={`font-mono text-[15px] tabular-nums ${tone}`}>
        {value == null ? "-" : value.toFixed(1)}
      </p>
    </div>
  );
}
