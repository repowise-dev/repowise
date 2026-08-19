import type { StatsHighlights } from "@repowise-dev/types/stats";
import {
  ChurnLedger,
  NLOC_HINT,
  OriginBlock,
  RecordsGrid,
  SizeClassHero,
  StatRibbon,
  type RibbonStat,
} from "@repowise-dev/ui/stats";
import { formatLOC, formatNumber } from "@repowise-dev/ui/lib/format";

/**
 * Tab 1 — what this repo *is*: scale, origin, lifetime churn, records.
 *
 * Deliberately carries no health scores, commit volume or dependency counts:
 * each of those has a page that owns the subject and shows it with filtering
 * and drill-down this page could never justify. What is left is the repo's
 * fingerprint, which nothing else draws.
 */
export function ByTheNumbersTab({ data }: { data: StatsHighlights }) {
  const { scale, origin, churn, records, rhythm } = data;

  const ribbon: RibbonStat[] = [
    { label: "Lines of code", value: formatLOC(scale.total_nloc), hint: NLOC_HINT },
    { label: "Files", value: formatNumber(scale.file_count) },
    { label: "Symbols", value: formatNumber(scale.symbol_count) },
    { label: "Modules", value: formatNumber(scale.module_count) },
    { label: "Languages", value: formatNumber(scale.language_count) },
  ];

  return (
    <div className="flex flex-col gap-8">
      <SizeClassHero scale={scale} repoName={data.repo.name} />

      <OriginBlock data={origin} />

      <StatRibbon stats={ribbon} />

      {churn && <ChurnLedger data={churn} />}

      <section className="flex flex-col gap-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h3 className="text-base font-semibold text-[var(--color-text-primary)]">
            Records &amp; superlatives
          </h3>
          {rhythm.longest_streak && (
            <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
              longest streak · {formatNumber(rhythm.longest_streak.days)} days
            </span>
          )}
        </div>
        <RecordsGrid records={records} />
      </section>
    </div>
  );
}
