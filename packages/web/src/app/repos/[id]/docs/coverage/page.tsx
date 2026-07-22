import type { Metadata } from "next";
import { DocsHeader } from "@/components/docs/docs-header";
import { CoverageDonut } from "@repowise-dev/ui/coverage/coverage-donut";
import { DriftBanner } from "@repowise-dev/ui/coverage/drift-banner";
import { ConfidenceVsFreshnessMatrix } from "@repowise-dev/ui/coverage/confidence-vs-freshness-matrix";
import { FreshnessTableWithRegenerate } from "@/components/coverage/freshness-table-wrapper";
import type { DocPage } from "@repowise-dev/types/docs";
import { MetricCard } from "@repowise-dev/ui/shared/metric-card";
import { listAllPages } from "@/lib/api/pages";
import { formatNumber } from "@repowise-dev/ui/lib/format";

export const metadata: Metadata = { title: "Doc freshness" };

export default async function CoveragePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let pages: Awaited<ReturnType<typeof listAllPages>> = [];

  try {
    // Auto-paginate past the 500-item API cap so the counts, donut, and the
    // "select all template pages" affordance see every page, not just the first
    // page of results.
    pages = await listAllPages(id);
  } catch {
    // API unavailable
  }

  const total = pages.length;

  const fresh = pages.filter((p) => p.freshness_status === "fresh").length;
  const stale = pages.filter((p) => p.freshness_status === "stale").length;
  const outdated = pages.filter((p) => p.freshness_status === "outdated").length;
  const freshPct = pages.length > 0 ? Math.round((fresh / pages.length) * 100) : 0;

  return (
    <div className="flex flex-col h-full">
      <DocsHeader>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          Freshness across {formatNumber(total)} pages
        </span>
      </DocsHeader>

      <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
        {/* Donut */}
        <div className="flex flex-col items-center gap-4 lg:w-56 shrink-0">
          <CoverageDonut fresh={fresh} stale={stale} outdated={outdated} />
          <p className="text-sm text-[var(--color-text-secondary)]">
            {freshPct}% of pages are fresh
          </p>
        </div>

        {/* Stat cards */}
        <div className="flex-1 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <MetricCard
            label="Fresh"
            value={formatNumber(fresh)}
            description={`${pages.length > 0 ? Math.round((fresh / pages.length) * 100) : 0}% of pages`}
          />
          <MetricCard
            label="Stale"
            value={formatNumber(stale)}
            description={`${pages.length > 0 ? Math.round((stale / pages.length) * 100) : 0}% of pages`}
          />
          <MetricCard
            label="Outdated"
            value={formatNumber(outdated)}
            description={`${pages.length > 0 ? Math.round((outdated / pages.length) * 100) : 0}% of pages`}
          />
        </div>
      </div>

      {/* Freshness distribution bar */}
      {pages.length > 0 && (
        <div>
          <p className="text-xs text-[var(--color-text-tertiary)] mb-1.5">Distribution</p>
          <div className="h-3 rounded-full overflow-hidden flex">
            <div className="bg-[var(--color-fresh)]" style={{ width: `${(fresh / pages.length) * 100}%` }} />
            <div className="bg-[var(--color-stale)]" style={{ width: `${(stale / pages.length) * 100}%` }} />
            <div className="bg-[var(--color-outdated)]" style={{ width: `${(outdated / pages.length) * 100}%` }} />
          </div>
          <div className="flex gap-4 mt-1.5 text-xs text-[var(--color-text-tertiary)]">
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-[var(--color-fresh)]" /> Fresh
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-[var(--color-stale)]" /> Stale
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-[var(--color-outdated)]" /> Outdated
            </span>
          </div>
        </div>
      )}

      <DriftBanner pages={pages as DocPage[]} />

      {pages.length > 0 && (
        <ConfidenceVsFreshnessMatrix pages={pages as DocPage[]} />
      )}

      <FreshnessTableWithRegenerate pages={pages} repoId={id} />
      </div>
    </div>
  );
}
