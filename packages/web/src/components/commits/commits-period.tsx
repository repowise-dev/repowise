import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { PeriodLede, type PeriodLedeCopy } from "@repowise-dev/ui/commits/period-lede";
import { RecentWins, type RecentWinsCopy } from "@repowise-dev/ui/commits/recent-wins";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import type { HistoryFilterValue } from "@repowise-dev/ui/commits/history-filters";
import type { PeriodHealthSummaryResponse, WinsResponse } from "@repowise-dev/types/history";
import { effectiveWindow } from "@/lib/commit-filters";

async function ledeCopy(): Promise<PeriodLedeCopy> {
  const t = await getTranslations("views.commits.periodLede");
  return {
    unit: (r) => t("unit", { n: r.total }),
    mix: (r, top, second) =>
      second && r.second
        ? t("mixTwo", { topShare: r.top?.share ?? 0, top, second, secondShare: r.second.share })
        : t("mix", { topShare: r.top?.share ?? 0, top }),
    better: (r) => t("better", { resolved: r.health.findingsBetter, worse: r.health.findingsWorse }),
    worse: (r) => t("worse", { resolved: r.health.findingsBetter, worse: r.health.findingsWorse }),
    flat: (r) => (r.health.findingsBetter === 0 ? t("flatNone") : t("flat", { resolved: r.health.findingsBetter })),
    notable: (subject, resolved) => t("notable", { subject, resolved }),
    coverage: (r) => t("coverage", { analysed: r.health.analysed, total: r.total }),
    empty: () => t("empty"),
    category: (id) => (t.has(`categories.${id}`) ? t(`categories.${id}`) : id),
  };
}

async function winsCopy(): Promise<RecentWinsCopy> {
  const t = await getTranslations("views.commits.wins");
  return {
    title: t("title"),
    findingsLabel: t("findingsLabel"),
    findingsUnit: (commits) => t("findingsUnit", { commits }),
    findingsWhy: (subject, resolved) => t("findingsWhy", { subject, resolved }),
    filesLabel: t("filesLabel"),
    filesUnit: t("filesUnit"),
    filesWhy: (path, before, after) =>
      t("filesWhy", { path, before: before.toFixed(1), after: after.toFixed(1) }),
    refactorsLabel: t("refactorsLabel"),
    refactorsUnit: t("refactorsUnit"),
    refactorsWhy: (symbol, before, after) =>
      before != null && after != null
        ? t("refactorsWhy", { symbol, before, after })
        : t("refactorsWhyPlain", { symbol }),
    none: t("none"),
    summary: (w) =>
      [
        w.findings_resolved.count > 0 && t("summaryResolved", { n: w.findings_resolved.count }),
        w.files_improved.count > 0 && t("summaryHealthier", { n: w.files_improved.count }),
        w.refactorings_landed.count > 0 && t("summaryRefactored", { n: w.refactorings_landed.count }),
      ]
        .filter(Boolean)
        .join(", "),
  };
}

/**
 * The commit view's lede: what happened in the window (volume, the dominant
 * kind of work, which way health moved, the notable change) beside the
 * window's wins, which sit to its right on wide screens and under it on
 * narrow ones. Both describe the selected window; the lede also follows
 * author, area and hotspots, the wins follow the author only, and their scope
 * line says so.
 */
export async function CommitsPeriod({
  repoId,
  filters,
  summary,
  wins,
}: {
  repoId: string;
  filters: HistoryFilterValue;
  summary: PeriodHealthSummaryResponse | null;
  wins: WinsResponse | null;
}) {
  if (!summary && !wins) return null;
  const t = await getTranslations("views.commits");
  const base = `/repos/${repoId}`;
  const period = t(`period.${effectiveWindow(filters)}`);
  const scope = [period, filters.author, filters.area || filters.hotspots ? t("wins.wholeRepo") : null]
    .filter(Boolean)
    .join(" · ");

  return (
    <section className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-10">
      {summary ? (
        <PeriodLede
          summary={summary}
          // The wins follow the window and author only; under an area or
          // hotspot filter their top commit may sit outside what the lede counts.
          wins={filters.area || filters.hotspots ? null : wins}
          period={period}
          commitHref={(sha) => `${base}/commits?commit=${sha}`}
          LinkComponent={Link}
          copy={await ledeCopy()}
        />
      ) : (
        <div />
      )}
      {wins && (
        <RecentWins
          collapsible
          wins={wins}
          scope={scope}
          LinkComponent={Link}
          routes={{
            commit: (sha) => `${base}/commits?commit=${sha}`,
            file: (path) => `${fileEntityPath(base, path)}?tab=health`,
            refactorings: `${base}/refactoring`,
          }}
          copy={await winsCopy()}
        />
      )}
    </section>
  );
}
