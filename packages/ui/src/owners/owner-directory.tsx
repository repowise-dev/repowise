"use client";

import * as React from "react";
import { Users, Search } from "lucide-react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { EmptyState } from "../shared/empty-state";
import { ResultsFooter } from "../shared/results-footer";
import { PageLede } from "../shared/page-lede";
import { OverviewSection, SectionLink } from "../overview/section";
import { ReadsColumn, type ReadItem } from "../overview/reads-column";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { OwnerTable, type OwnerSortKey } from "./owner-table";
import { OwnershipDistributionBar } from "./ownership-distribution-bar";

export type { OwnerSortKey };

export interface OwnerDirectoryFilters {
  q: string;
  sort: OwnerSortKey;
}

export interface OwnerDirectoryProps {
  owners: OwnerListEntry[];
  /** Optional full owner set for the distribution bar and the lede (e.g.
   *  fetched via listAllOwners when the contributor count is small). Falls
   *  back to `owners` when omitted. */
  distributionOwners?: OwnerListEntry[];
  isLoading: boolean;
  isValidating: boolean;
  total: number;
  hasMore: boolean;
  filters: OwnerDirectoryFilters;
  onFiltersChange: (next: OwnerDirectoryFilters) => void;
  onLoadMore: () => void;
  onSelect: (owner: OwnerListEntry) => void;
  /** Preferred over `onSelect`: gives every row a real URL. */
  hrefFor?: (owner: OwnerListEntry) => string;
  /** Base path for the section jump links, e.g. `/repos/42`. */
  base?: string;
  LinkComponent?: React.ElementType | undefined;
}

const INACTIVE_DAYS = 90;

function daysSince(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return (Date.now() - t) / 86_400_000;
}

/**
 * Who knows what, as a lede plus one table.
 *
 * This page used to open on four bordered counters — one of them "Bus-factor
 * risk: 12" in red — above a 3-up grid of contributor cards. The counters told
 * a reader to worry without saying what about, and the grid could not answer
 * the question people actually arrive with, which is how two contributors
 * compare: that needs the figures in a column, which a card grid makes
 * impossible. Both are now one figure with a sentence, and one aligned table.
 */
export function OwnerDirectory({
  owners,
  distributionOwners,
  isLoading,
  isValidating,
  total,
  hasMore,
  filters,
  onFiltersChange,
  onLoadMore,
  onSelect,
  hrefFor,
  base,
  LinkComponent,
}: OwnerDirectoryProps) {
  // The lede is a claim about the whole repo, so it is computed off the full
  // set when the caller has one. Computed off the loaded page instead, "68% of
  // files" would silently mean "68% of the 30 rows currently rendered" and
  // would change as you scrolled — a figure that moves while the data does not
  // is a wrong figure, not a live one.
  const source = distributionOwners ?? owners;
  const complete = distributionOwners != null || (!hasMore && filters.q === "");

  const summary = React.useMemo(() => {
    const sorted = [...source].sort((a, b) => b.files_owned - a.files_owned);
    const totalFiles = sorted.reduce((s, o) => s + o.files_owned, 0);
    const top3 = sorted.slice(0, 3).reduce((s, o) => s + o.files_owned, 0);
    const soleFiles = sorted.reduce((s, o) => s + o.bus_factor_risk_files, 0);
    const soleFilesInactive = sorted.reduce((s, o) => {
      const d = daysSince(o.last_commit_at);
      return d != null && d > INACTIVE_DAYS ? s + o.bus_factor_risk_files : s;
    }, 0);
    const siloOwners = sorted.filter((o) => o.silo_modules > 0).length;
    const siloModules = sorted.reduce((s, o) => s + o.silo_modules, 0);
    const deadLines = sorted.reduce((s, o) => s + o.dead_code_lines_owned, 0);
    const deadOwners = sorted.filter((o) => o.dead_code_lines_owned > 0).length;
    const deadFiles = sorted.reduce((s, o) => s + o.dead_code_files_owned, 0);
    const active = sorted.filter((o) => o.commit_count_90d > 0).length;
    const hotspots = sorted.reduce((s, o) => s + o.hotspots_owned, 0);
    const commits90d = sorted.reduce((s, o) => s + o.commit_count_90d, 0);
    return {
      deadFiles,
      hotspots,
      commits90d,
      topName: sorted[0]?.name ?? null,
      topFiles: sorted[0]?.files_owned ?? 0,
      totalFiles,
      top3Pct: totalFiles > 0 ? Math.round((top3 / totalFiles) * 100) : 0,
      soleFiles,
      soleFilesInactive,
      siloOwners,
      siloModules,
      deadLines,
      deadOwners,
      active,
    };
  }, [source]);

  // Built rather than declared: a repo with no dead code and no silos should
  // get a shorter column, not four rows of zeroes. An empty state that says
  // "0" is claiming a measurement; omitting the row says there is nothing to
  // report, which is what is true.
  const reads: ReadItem[] = [];
  if (summary.soleFiles > 0) {
    reads.push({
      key: "sole",
      label: "Sole-owned files",
      value: summary.soleFiles.toLocaleString(),
      ...(summary.totalFiles > 0
        ? { unit: `of ${summary.totalFiles.toLocaleString()}` }
        : {}),
      why:
        summary.soleFilesInactive > 0
          ? `One author, no second reader. ${summary.soleFilesInactive.toLocaleString()} belong to someone who has not committed in ${INACTIVE_DAYS} days.`
          : "One author and no second reader, though all of them belong to someone still active.",
      href: base ? `${base}/owners` : "#",
    });
  }
  if (summary.siloModules > 0) {
    reads.push({
      key: "silo",
      label: "Silo modules",
      value: summary.siloModules.toLocaleString(),
      unit: `${summary.siloOwners} ${summary.siloOwners === 1 ? "person" : "people"}`,
      why: "A single person owns more than 80% of the module.",
      href: base ? `${base}/modules` : "#",
    });
  }
  reads.push({
    key: "active",
    label: "Active this quarter",
    value: summary.active.toLocaleString(),
    unit: `of ${total.toLocaleString()}`,
    why: `Committed in the last ${INACTIVE_DAYS} days. The rest are history, not staffing.`,
    href: base ? `${base}/commits` : "#",
  });
  if (summary.deadLines > 0) {
    reads.push({
      key: "dead",
      label: "Dead lines owned",
      value: summary.deadLines.toLocaleString(),
      unit: `${summary.deadOwners} ${summary.deadOwners === 1 ? "person" : "people"}`,
      why: "Unreachable code still attributed to whoever wrote it.",
      href: base ? `${base}/code-health?tab=dead-code` : "#",
    });
  }

  // Deliberately disjoint from `reads` above. The first cut of this row
  // restated Active, Sole-owned and Silo modules from the column immediately
  // beside it, so three of five cells were the same figure twice on one
  // screen. StatRibbon drops empty-valued entries itself, so an unmeasured
  // figure leaves no gap.
  const ribbon: RibbonStat[] = [
    {
      label: "Contributors",
      value: total.toLocaleString(),
      hint: "Distinct commit authors in the indexed history",
    },
    {
      label: "Files attributed",
      value: summary.totalFiles > 0 ? summary.totalFiles.toLocaleString() : "",
    },
    {
      label: "Hotspots owned",
      value: summary.hotspots > 0 ? summary.hotspots.toLocaleString() : "",
      hint: "Owned files that are also high-churn",
    },
    {
      label: "Commits 90d",
      value: summary.commits90d > 0 ? summary.commits90d.toLocaleString() : "",
    },
    {
      label: "Dead-code files",
      value: summary.deadFiles > 0 ? summary.deadFiles.toLocaleString() : "",
      hint: "Files carrying unreachable code, counted separately from the line total",
    },
  ];

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      {summary.totalFiles > 0 && (
        <section className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-10">
          <PageLede
            label="Knowledge concentration"
            value={`${summary.top3Pct}%`}
            band={{ label: summary.top3Pct >= 60 ? "Concentrated" : "Spread" }}
            {...(complete ? {} : { unit: "of the contributors loaded so far" })}
          >
            <p>
              The top three of {total.toLocaleString()} contributors own{" "}
              <strong className="font-semibold text-[var(--color-text-primary)]">
                {summary.top3Pct}%
              </strong>{" "}
              of the {summary.totalFiles.toLocaleString()} attributed files
              {summary.topName
                ? `, with ${summary.topName} on ${summary.topFiles.toLocaleString()} of them`
                : ""}
              . Ownership is attributed by who wrote the surviving lines, not by who
              committed last, so a formatting sweep does not hand someone a file they
              have never read.
              {summary.soleFiles > 0 && (
                <>
                  {" "}
                  <strong className="font-semibold text-[var(--color-text-primary)]">
                    {summary.soleFiles.toLocaleString()}
                  </strong>{" "}
                  of those files have a single author and no second reader, which is
                  where the knowledge actually walks out.
                </>
              )}
            </p>
          </PageLede>
          {reads.length > 0 && <ReadsColumn items={reads} LinkComponent={LinkComponent} />}
        </section>
      )}

      {/* Same guard as the lede. Before an index lands, every figure here is
          zero and StatRibbon's empty-value filter leaves a single "Contributors
          0" cell stranded in a five-column row. Nothing measured, nothing
          drawn. */}
      {summary.totalFiles > 0 && (
        <StatRibbon stats={ribbon} LinkComponent={LinkComponent} />
      )}

      {source.length > 0 && (
        <OverviewSection
          title="How ownership is spread"
          description="Share of attributed files per contributor, largest first, with the long tail collapsed into one segment."
          {...(base
            ? {
                action: (
                  <SectionLink href={`${base}/owners`} LinkComponent={LinkComponent}>
                    Ownership map
                  </SectionLink>
                ),
              }
            : {})}
        >
          <OwnershipDistributionBar
            owners={source}
            totalContributors={total}
            {...(hrefFor ? { hrefFor } : { onSelect })}
            {...(LinkComponent ? { LinkComponent } : {})}
          />
        </OverviewSection>
      )}

      <OverviewSection
        title="Everyone"
        description="A dot marks sole-owned files held by someone who has stopped committing, so a clean column means nothing to chase."
        action={
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            {total.toLocaleString()} {total === 1 ? "contributor" : "contributors"}
          </span>
        }
      >
        <div className="relative max-w-sm">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-[var(--color-text-tertiary)]" />
          <Input
            value={filters.q}
            onChange={(e) => onFiltersChange({ ...filters, q: e.target.value })}
            placeholder="Filter by name or email…"
            aria-label="Filter contributors by name or email"
            className="pl-8"
          />
        </div>

        {isLoading && owners.length === 0 ? (
          // Row-height skeletons, not tiles: a skeleton whose shape misses the
          // real layout reflows when content lands, which reads as slower than
          // showing nothing.
          <div className="flex flex-col gap-px">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-[54px] w-full" />
            ))}
          </div>
        ) : owners.length === 0 ? (
          <EmptyState
            icon={<Users className="h-6 w-6" />}
            title="No contributors match"
            description={
              filters.q
                ? "Nothing matches that filter. Clear it to see everyone."
                : "Contributors land with the first git index."
            }
          />
        ) : (
          <OwnerTable
            owners={owners}
            sort={filters.sort}
            onSortChange={(sort) => onFiltersChange({ ...filters, sort })}
            hrefFor={hrefFor}
            onSelect={hrefFor ? undefined : onSelect}
            LinkComponent={LinkComponent}
          />
        )}

        <ResultsFooter
          shown={owners.length}
          total={total}
          hasMore={hasMore}
          loading={isValidating && !isLoading}
          onLoadMore={onLoadMore}
          noun="contributor"
        />
      </OverviewSection>
    </div>
  );
}
