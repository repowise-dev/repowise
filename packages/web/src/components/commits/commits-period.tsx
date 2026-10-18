import Link from "next/link";
import { getTranslations } from "next-intl/server";
import {
  PeriodHealthHeadline,
  type PeriodHealthCopy,
} from "@repowise-dev/ui/commits/period-health-headline";
import { RecentWins, type RecentWinsCopy } from "@repowise-dev/ui/commits/recent-wins";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import type { HistoryFilterValue } from "@repowise-dev/ui/commits/history-filters";
import type { PeriodHealthSummaryResponse, WinsResponse } from "@repowise-dev/types/history";
import { effectiveWindow } from "@/lib/commit-filters";

async function headlineCopy(): Promise<PeriodHealthCopy> {
  const t = await getTranslations("views.commits.headline");
  return {
    unit: (r) => t("unit", { n: r.better }),
    good: (r) => t("good", { better: r.better, resolved: r.findingsBetter }),
    mixed: (r) => t("mixed", { better: r.better, worse: r.worse, both: r.both, neutral: r.neutral }),
    quiet: (r) => t("quiet", { analysed: r.analysed }),
    catchingUpUnit: (r) => t("catchingUpUnit", { total: r.total }),
    catchingUp: () => t("catchingUp"),
    empty: () => t("empty"),
    unscanned: (r) => t("unscanned", { n: r.unscanned }),
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
  };
}

/**
 * The commit view's lede: the period headline (improvements first) beside the
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
  if (!summary && !wins) return null;
  const t = await getTranslations("views.commits");
  const base = `/repos/${repoId}`;
  const period = t(`period.${effectiveWindow(filters)}`);
  const scope = [period, filters.author, filters.area || filters.hotspots ? t("wins.wholeRepo") : null]
    .filter(Boolean)
    .join(" · ");

  return (
    <section className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-10">
      {summary ? <PeriodHealthHeadline summary={summary} period={period} copy={await headlineCopy()} /> : <div />}
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
          copy={await winsCopy()}
        />
      )}
    </section>
  );
}
