import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { PeriodHealthHeadline } from "@repowise-dev/ui/commits/period-health-headline";
import { RecentWins } from "@repowise-dev/ui/commits/recent-wins";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import type { HistoryFilterValue } from "@repowise-dev/ui/commits/history-filters";
import type { PeriodHealthSummaryResponse, WinsResponse } from "@repowise-dev/types/history";
import { effectiveWindow } from "@/lib/commit-filters";

/**
 * The Commits page's lede: the period headline (improvements first) beside the
 * window's wins. Both describe the selected window; the headline also follows
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
  const t = await getTranslations("views.commits");
  if (!summary && !wins) return null;
  const base = `/repos/${repoId}`;
  const period = t(`period.${effectiveWindow(filters)}`);
  const scope = [
    period,
    filters.author ?? null,
    filters.area || filters.hotspots ? t("wins.wholeRepo") : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-10">
      {summary ? (
        <PeriodHealthHeadline
          summary={summary}
          period={period}
          copy={{
            unit: (r) => t("headline.unit", { n: r.better }),
            good: (r) => t("headline.good", { better: r.better, resolved: r.findingsBetter }),
            mixed: (r) =>
              t("headline.mixed", { better: r.better, worse: r.worse, both: r.both, neutral: r.neutral }),
            quiet: (r) => t("headline.quiet", { analysed: r.analysed }),
            empty: (r) => t("headline.empty", { total: r.total }),
            unscanned: (r) => t("headline.unscanned", { n: r.unscanned }),
          }}
        />
      ) : (
        <div />
      )}
      {wins && (
        <RecentWins
          wins={wins}
          scope={scope}
          LinkComponent={Link}
          routes={{
            commit: (sha) => `${base}/commits?commit=${sha}`,
            file: (path) => `${fileEntityPath(base, path)}?tab=health`,
            refactorings: `${base}/refactoring`,
          }}
          copy={{
            title: t("wins.title"),
            findingsLabel: t("wins.findingsLabel"),
            findingsUnit: (commits) => t("wins.findingsUnit", { commits }),
            findingsWhy: (subject, resolved) => t("wins.findingsWhy", { subject, resolved }),
            filesLabel: t("wins.filesLabel"),
            filesUnit: t("wins.filesUnit"),
            filesWhy: (path, before, after) =>
              t("wins.filesWhy", { path, before: before.toFixed(1), after: after.toFixed(1) }),
            refactorsLabel: t("wins.refactorsLabel"),
            refactorsUnit: t("wins.refactorsUnit"),
            refactorsWhy: (symbol, before, after) =>
              before != null && after != null
                ? t("wins.refactorsWhy", { symbol, before, after })
                : t("wins.refactorsWhyPlain", { symbol }),
            none: t("wins.none"),
          }}
        />
      )}
    </section>
  );
}
