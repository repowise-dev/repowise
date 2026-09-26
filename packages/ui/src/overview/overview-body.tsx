import * as React from "react";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";
import { getDefaultHref } from "../dashboard/attention-href";
import { formatNumber } from "../lib/format";
import { fileEntityPath } from "../shared/entity";
import { StatRibbon } from "../stats/stat-ribbon";
import { AttentionAreas } from "./attention-areas";
import { AttentionRows } from "./attention-rows";
import { CommitRows, DecisionRows, type CommitRow } from "./activity-lists";
import { ChangeLine } from "./change-line";
import { ExploreList } from "./explore-list";
import { HealthLede } from "./health-lede";
import { HotspotTable } from "./hotspot-table";
import { LanguageBar } from "./language-bar";
import {
  buildChangeStats,
  buildExplore,
  buildLanguageDistribution,
  buildReads,
  buildRibbon,
  previousSnapshotAt,
  resolveOverviewRoutes,
  type OverviewRoutes,
} from "./overview-model";
import { ReadsColumn } from "./reads-column";
import { OverviewSection, SectionLink } from "./section";

/** How many rows each list section shows before deferring to its own page. */
const ATTENTION_ROWS = 6;
const HOTSPOT_ROWS = 5;
const DECISION_ROWS = 6;

export interface OverviewBodySlots {
  /** The identity header. Each host builds its own: OSS carries reindex
   *  actions, hosted carries a snapshot chip, and neither is expressible as a
   *  prop of the other. */
  header?: React.ReactNode;
  /** Under the reads column. Hosted puts its MCP connect card here. */
  afterReads?: React.ReactNode;
  /** A section between Composition and Explore. Hosted's index-coverage
   *  panel. */
  beforeExplore?: React.ReactNode;
  /** Contents of the "Ask this codebase" section. Omitted where the host
   *  routes to a chat page from Explore instead, and the section then does not
   *  render at all rather than rendering empty. */
  ask?: React.ReactNode;
}

export interface OverviewBodyProps {
  summary: OverviewSummaryResponse;
  routes: OverviewRoutes;
  /** Recent commits, already fetched and bounded by the host. */
  commits?: CommitRow[];
  /** Total lines of code. The two hosts source this differently, so it is a
   *  prop rather than a payload read. */
  totalNloc?: number | null;
  LinkComponent?: React.ElementType;
  slots?: OverviewBodySlots;
}

/**
 * The repo Overview, once its data has arrived.
 *
 * Renders only — no fetching, no hooks, no host imports. That is what lets OSS
 * call it from a server component and hosted call it from inside a `"use
 * client"` SWR tree, which was the reason the two pages had been copies of
 * each other rather than one component: hosted cannot be a server component
 * (its payload arrives through SWR, and private snapshots have no server-side
 * token), so anything that fetches cannot be shared, and anything that does
 * not fetch can.
 *
 * Section order answers three questions in sequence: what state is this
 * repository in, what needs me, and where do I go. "Needs attention" sits
 * directly under the health hero rather than fourth, because the page is
 * mostly opened by someone who was here last week and wants the delta, not by
 * a stranger who wants the tour.
 */
export function OverviewBody({
  summary,
  routes,
  commits = [],
  totalNloc,
  LinkComponent,
  slots = {},
}: OverviewBodyProps) {
  const resolved = resolveOverviewRoutes(routes);
  const { base } = resolved;
  const { stats, health } = summary;

  const reads = buildReads(summary, routes);
  const ribbon = buildRibbon(summary, routes, totalNloc);
  const explore = buildExplore(summary, routes);
  const changeStats = buildChangeStats(summary);
  const langDistribution = buildLanguageDistribution(summary);

  // The list is capped per source server-side, so its length is not a total.
  // Falling back to the row count on a server that predates the summary keeps
  // the old (correct-for-that-payload) behaviour rather than rendering
  // nothing.
  const attentionTotal = summary.attention_summary?.total ?? summary.attention.length;
  const areas = summary.attention_summary?.areas ?? [];

  return (
    <div className="mx-auto flex w-full max-w-[1280px] flex-col gap-6 p-[var(--page-pad)] sm:gap-8">
      {slots.header}

      <ChangeLine since={previousSnapshotAt(summary)} stats={changeStats} />

      {/* Health keeps the largest number and the leftmost position. The column
          beside it carries the other reasons someone opens Overview, so a
          visitor who came for docs or costs finds their figure at the same
          altitude rather than three scrolls down. */}
      <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-12">
        <HealthLede
          score={health.average_health}
          maintainability={health.maintainability_average}
          performance={health.performance_average}
          hotspotHealth={health.hotspot_health}
          hotspotCount={stats.hotspot_count}
          fileCount={stats.file_count}
          href={`${base}/code-health`}
          LinkComponent={LinkComponent}
        />
        <div className="flex flex-col gap-6">
          <ReadsColumn items={reads} LinkComponent={LinkComponent} />
          {slots.afterReads}
        </div>
      </div>

      {(areas.length > 0 || summary.attention.length > 0) && (
        <OverviewSection
          title="Needs attention"
          description="Every detector, by area of work, each leading with the worst thing in it: code health and the test-quality biomarkers, security, refactoring, documentation drift, decision records, ownership, and dead code."
          // No section link: these span seven pages, so there is no single
          // destination that holds "all of them", and a count linking to one
          // of them would be a lie about where the rest live.
          action={
            <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
              {formatNumber(attentionTotal)} open
            </span>
          }
        >
          {areas.length > 0 ? (
            <AttentionAreas areas={areas} prefix={base} LinkComponent={LinkComponent} />
          ) : (
            // A server predating the area rollup still sends ranked items, and
            // rendering those is strictly better than rendering nothing.
            <AttentionRows
              items={summary.attention.slice(0, ATTENTION_ROWS)}
              hrefFor={(item) => getDefaultHref(item, base)}
              LinkComponent={LinkComponent}
            />
          )}
        </OverviewSection>
      )}

      <StatRibbon stats={ribbon} LinkComponent={LinkComponent} />

      <OverviewSection
        title="Recent activity"
        action={
          <SectionLink href={`${base}/commits`} LinkComponent={LinkComponent}>
            All commits
          </SectionLink>
        }
      >
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 md:gap-10">
          <div className="flex min-w-0 flex-col gap-2">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Commits
            </h3>
            <CommitRows
              commits={commits}
              hrefFor={(sha) => `${base}/commits?commit=${sha}`}
              LinkComponent={LinkComponent}
            />
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Decisions
            </h3>
            <DecisionRows
              decisions={summary.recent_decisions.slice(0, DECISION_ROWS)}
              hrefFor={(decisionId) => `${base}/decisions/${encodeURIComponent(decisionId)}`}
              LinkComponent={LinkComponent}
            />
          </div>
        </div>
      </OverviewSection>

      {summary.top_hotspots.length > 0 && (
        <OverviewSection
          title="Where the risk concentrates"
          description="Ranked by prior bug fixes and change frequency, mined from full git history rather than from the code alone."
          action={
            <SectionLink href={`${base}/code-health?tab=triage`} LinkComponent={LinkComponent}>
              {`All ${formatNumber(stats.hotspot_count)} hotspots`}
            </SectionLink>
          }
        >
          <HotspotTable
            hotspots={summary.top_hotspots.slice(0, HOTSPOT_ROWS)}
            hrefFor={(path) => fileEntityPath(base, path)}
            LinkComponent={LinkComponent}
          />
        </OverviewSection>
      )}

      {summary.languages.length > 0 && (
        <OverviewSection
          title="Composition"
          action={
            <SectionLink
              href={resolved.languageGraph}
              LinkComponent={LinkComponent}
            >
              Open the graph
            </SectionLink>
          }
        >
          <LanguageBar distribution={langDistribution} />
        </OverviewSection>
      )}

      {slots.beforeExplore}

      {slots.ask && <OverviewSection title="Ask this codebase">{slots.ask}</OverviewSection>}

      <OverviewSection title="Explore this codebase">
        <ExploreList entries={explore} LinkComponent={LinkComponent} />
      </OverviewSection>
    </div>
  );
}
