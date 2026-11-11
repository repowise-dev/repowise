import type { Metadata } from "next";
import { DocsHeader } from "@/components/docs/docs-header";
import { CoverageDonut } from "@repowise-dev/ui/coverage/coverage-donut";
import { DriftBanner } from "@repowise-dev/ui/coverage/drift-banner";
import { ConfidenceVsFreshnessMatrix } from "@repowise-dev/ui/coverage/confidence-vs-freshness-matrix";
import { FreshnessTableWithRegenerate } from "@/components/coverage/freshness-table-wrapper";
import type { DocPage } from "@repowise-dev/types/docs";
import { MetricCard } from "@repowise-dev/ui/shared/metric-card";
import { ProportionBar } from "@repowise-dev/ui/shared/proportion-bar";
import { listAllPages } from "@/lib/api/pages";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { getTranslations } from "next-intl/server";
import { HostedNudgeSlot } from "@/components/hosted/hosted-nudge-slot";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { PageFrame } from "@repowise-dev/ui/shared/page-shell";

export const metadata: Metadata = { title: "Doc freshness" };

export default async function CoveragePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const t = await getTranslations("views.docsFreshness");

  let pages: Awaited<ReturnType<typeof listAllPages>> = [];
  let failed = false;

  try {
    // Auto-paginate past the 500-item API cap so the counts, donut, and the
    // freshness table see every page, not just the first page of results.
    pages = await listAllPages(id);
  } catch {
    failed = true;
  }

  const total = pages.length;

  const fresh = pages.filter((p) => p.freshness_status === "fresh").length;
  const stale = pages.filter((p) => p.freshness_status === "stale").length;
  const outdated = pages.filter((p) => p.freshness_status === "outdated").length;
  /** Share of the indexed pages one bucket holds, rounded to a whole percent. */
  const pctOf = (count: number) =>
    pages.length > 0 ? Math.round((count / pages.length) * 100) : 0;
  const freshPct = pctOf(fresh);

  return (
    <div className="flex flex-col h-full">
      <DocsHeader>
        {!failed && (
          <span className="text-xs text-[var(--color-text-tertiary)]">
            {t("header", { count: formatNumber(total) })}
          </span>
        )}
      </DocsHeader>

      <div className="min-h-0 flex-1 overflow-y-auto">
      {failed ? (
        <PageFrame maxWidth="wide">
          <ApiError title={t("loadErrorTitle")} message={t("loadErrorBody")} />
        </PageFrame>
      ) : (
      <PageFrame maxWidth="wide">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
        {/* Donut */}
        <div className="flex flex-col items-center gap-4 lg:w-56 shrink-0">
          <CoverageDonut fresh={fresh} stale={stale} outdated={outdated} />
          <p className="text-sm text-[var(--color-text-secondary)]">
            {t("freshPct", { pct: freshPct })}
          </p>
        </div>

        {/* Stat cards */}
        <div className="flex-1 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <MetricCard
            label={t("fresh")}
            value={formatNumber(fresh)}
            description={t("pctOfPages", { pct: pctOf(fresh) })}
          />
          <MetricCard
            label={t("stale")}
            value={formatNumber(stale)}
            description={t("pctOfPages", { pct: pctOf(stale) })}
          />
          <MetricCard
            label={t("outdated")}
            value={formatNumber(outdated)}
            description={t("pctOfPages", { pct: pctOf(outdated) })}
          />
        </div>
      </div>

      {/* Freshness distribution bar */}
      {pages.length > 0 && (
        <div>
          <p className="text-xs text-[var(--color-text-tertiary)] mb-1.5">
            {t("distribution")}
          </p>
          <ProportionBar
            label={t("distribution")}
            sort={false}
            segments={[
              { key: "fresh", label: t("fresh"), value: fresh, color: "var(--color-fresh)" },
              { key: "stale", label: t("stale"), value: stale, color: "var(--color-stale)" },
              { key: "outdated", label: t("outdated"), value: outdated, color: "var(--color-outdated)" },
            ]}
          />
        </div>
      )}

      <DriftBanner pages={pages as DocPage[]} />
      <HostedNudgeSlot candidates={[stale + outdated > 0 && "stale"]} repoId={id} />

      {pages.length > 0 && (
        <ConfidenceVsFreshnessMatrix pages={pages as DocPage[]} />
      )}

      <FreshnessTableWithRegenerate pages={pages} repoId={id} />
      </PageFrame>
      )}
      </div>
    </div>
  );
}
