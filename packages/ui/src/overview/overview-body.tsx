import * as React from "react";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";
import { formatNumber } from "../lib/format";
import { fileEntityPath } from "../shared/entity";
import { PageFrame } from "../shared/page-shell";
import { AttentionSection } from "./attention-section";
import type { CommitRow } from "./activity-lists";
import { ChangeLine } from "./change-line";
import { ExploreCards } from "./explore-cards";
import { HealthLede } from "./health-lede";
import { HotspotTable } from "./hotspot-table";
import {
  buildChangeStats,
  buildExplore,
  buildExploreCards,
  buildReads,
  previousSnapshotAt,
  resolveOverviewRoutes,
  type OverviewRoutes,
} from "./overview-model";
import { ReadsColumn } from "./reads-column";
import { OverviewSection, SectionLink } from "./section";

/** Hotspot rows shown before deferring to Code Health. */
const HOTSPOT_ROWS = 5;

export interface OverviewBodySlots {
  /** The identity header. Each host builds its own: OSS carries reindex
   *  actions, hosted carries a snapshot chip, and neither is expressible as a
   *  prop of the other. */
  header?: React.ReactNode;
  /** Under the reads column. Hosted puts its MCP connect card here. */
  afterReads?: React.ReactNode;
  /** A section after Do next and recent wins. Hosted's index-coverage
   *  panel, OSS's publish panel. */
  beforeExplore?: React.ReactNode;
  /** The ask box, full width under the health lede. Omitted where the host
   *  routes to a chat page from Explore instead. */
  ask?: React.ReactNode;
  /** The "Do next" list, bound to the host's routes and write path. When
   *  present it leads the page and the per-detector areas fold into an
   *  inventory lower down; when absent (a host not yet serving actions) the
   *  areas keep their old place. */
  actions?: React.ReactNode;
  /** What got better recently, directly under "Do next". Omitted, nothing
   *  renders in its place. */
  wins?: React.ReactNode;
}

export interface OverviewBodyProps {
  summary: OverviewSummaryResponse;
  routes: OverviewRoutes;
  /** @deprecated Ignored; recent commits live on the Commits page. */
  commits?: CommitRow[];
  /** @deprecated Ignored; size figures live on the Stats page. */
  totalNloc?: number | null;
  LinkComponent?: React.ElementType;
  slots?: OverviewBodySlots;
  /** Section titles and labels. English by default. */
  copy?: Partial<OverviewBodyCopy>;
}

export interface OverviewBodyCopy {
  exploreTitle: string;
  exploreMore: string;
}

export const OVERVIEW_BODY_COPY: OverviewBodyCopy = {
  exploreTitle: "Explore this codebase",
  exploreMore: "Also",
};

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
 * Short and action-first: the health reading, the ask box, doors into the
 * pages that own each subject, then what to do now and what got better. Anything
 * a deeper page already shows in full (commit lists, decisions, size and
 * language figures) is a link from Explore rather than a copy here.
 *
 * When the host supplies actions, "Do next" leads and the per-detector areas
 * fold into an inventory below it.
 */
export function OverviewBody({
  summary,
  routes,
  LinkComponent,
  slots = {},
  copy,
}: OverviewBodyProps) {
  const c = { ...OVERVIEW_BODY_COPY, ...copy };
  const resolved = resolveOverviewRoutes(routes);
  const { base } = resolved;
  const { stats, health } = summary;

  const reads = buildReads(summary, routes);
  const cards = buildExploreCards(summary, routes);
  const carded = new Set(cards.map((card) => card.key));
  const moreDoors = buildExplore(summary, routes).filter((e) => !carded.has(e.key));
  const changeStats = buildChangeStats(summary);

  return (
    <PageFrame>
      {slots.header}

      <ChangeLine since={previousSnapshotAt(summary)} stats={changeStats} />

      <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-12">
        <HealthLede
          score={health.average_health}
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

      {slots.ask}

      <OverviewSection title={c.exploreTitle}>
        <ExploreCards
          cards={cards}
          more={moreDoors}
          moreLabel={c.exploreMore}
          LinkComponent={LinkComponent}
        />
      </OverviewSection>

      {slots.actions ?? (
        <AttentionSection summary={summary} base={base} LinkComponent={LinkComponent} />
      )}

      {slots.wins}

      {/* A host without actions keeps the hotspot table: with "Do next", its
          files arrive as fragile-file and bug-fix concentration actions with
          the reason attached, and a second "riskiest files" list would
          disagree with Code Health's. */}
      {!slots.actions && summary.top_hotspots.length > 0 && (
        <OverviewSection
          title="Where the risk concentrates"
          description="Ranked by prior bug fixes and change frequency, mined from full git history rather than from the code alone."
          action={
            <SectionLink href={`${base}/code-health`} LinkComponent={LinkComponent}>
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

      {slots.beforeExplore}

      {/* The areas as the inventory behind "Do next", folded at the bottom
          because the list above already says what to do. */}
      {slots.actions && (
        <AttentionSection summary={summary} base={base} inventory LinkComponent={LinkComponent} />
      )}
    </PageFrame>
  );
}
