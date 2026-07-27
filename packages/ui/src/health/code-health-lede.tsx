/**
 * The Code Health page's opening read: one figure large enough to lead, and the
 * sentences that make it mean something.
 *
 * It replaces four separate containers that used to stack above the map — a
 * collapsible "can you trust this score?" banner, three bordered signal tiles,
 * and a bordered strip of operational stats. Between them they carried six
 * numbers at near-identical weight behind five uppercase labels, which is the
 * box-soup failure: everything claims the same importance, so nothing leads,
 * and the page needed borders to produce the structure a type scale should have
 * given it for free.
 *
 * The prose is not decoration here. "329 risks" reads as alarming on its own;
 * "329 static performance risks, scored separately and never blended into the
 * defect number" reads as informative. Same figure. The accuracy claim in
 * particular only means anything next to its base rate — a 72% hit rate is
 * excellent against a 20% baseline and unremarkable against a 70% one — so it
 * is a sentence rather than a badge.
 */

import type {
  DefectAccuracy,
  HealthDistribution,
  HealthOverviewSummary,
} from "@repowise-dev/types/health";
import { PageLede } from "../shared/page-lede";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
// Bands come from the one shared function on purpose. Two surfaces disagreeing
// about where "Good" starts is worse than the duplication that would avoid it.
import { healthBand } from "../overview/health-lede";
import { formatNumber } from "../lib/format";
import { scoreTextColor } from "./tokens";
import { HealthDistributionBar } from "./health-distribution-bar";

export interface CodeHealthLedeProps {
  summary: HealthOverviewSummary;
  /** Null when the repo lacks the defect history to make an honest claim. */
  accuracy?: DefectAccuracy | null;
  /** NLOC-weighted split across the bands, shown under the score. */
  distribution?: HealthDistribution | null;
  /** Rendered under the prose — the host's pillar deep-links. */
  action?: React.ReactNode;
}

/** "3 months" / "1 month", from a day count. */
function windowLabel(days: number): string {
  const months = Math.max(1, Math.round(days / 30));
  return months === 1 ? "month" : `${months} months`;
}

export function CodeHealthLede({
  summary,
  accuracy,
  distribution,
  action,
}: CodeHealthLedeProps) {
  const band = healthBand(summary.average_health);
  const maint = summary.maintainability_average;
  const perf = summary.performance_average;
  const perfFindings = summary.performance_findings ?? 0;
  const hotspot = summary.hotspot_health;

  // Assembled rather than interpolated inline: a repo can have measured one
  // pillar and not the other, and the naive version produces "The three are
  // scored separately" when there are two of them.
  const pillars: string[] = [];
  if (maint != null) pillars.push(`maintainability ${maint.toFixed(1)}`);
  if (perf != null) pillars.push(`static performance risk ${perf.toFixed(1)}`);

  const stats: RibbonStat[] = [
    { label: "Files", value: formatNumber(summary.file_count) },
    {
      label: "Maintainability",
      value: maint == null ? "" : `${maint.toFixed(1)}`,
      valueColor: maint == null ? undefined : scoreTextColor(maint),
      hint: "Smells that raise change-cost without predicting bugs. Scored on its own, never blended into the defect number.",
    },
    {
      label: "Performance risk",
      value: perf == null ? "" : formatNumber(perfFindings),
      hint: "Open static performance risks: a DB, network, filesystem or subprocess call per loop iteration, found across function boundaries. High precision, low recall.",
    },
    {
      label: "Hotspot health",
      value: hotspot == null ? "" : hotspot.toFixed(1),
      valueColor: hotspot == null ? undefined : scoreTextColor(hotspot),
      hint: "The score averaged over the repo's churn hotspots only. How healthy is the code you touch most?",
    },
    { label: "Open findings", value: formatNumber(summary.open_findings) },
  ];

  return (
    <div className="flex flex-col gap-6">
      <PageLede
        label="Defect risk"
        value={summary.average_health.toFixed(1)}
        valueColor={band.color}
        unit="out of 10"
        band={band}
        action={action}
        layout="beside"
        // The one second read that belongs to the score itself: how the repo's
        // code volume splits across the bands. A 7.5 average hides whether that
        // is everything-mediocre or mostly-healthy-with-a-bad-corner, and those
        // are different problems.
        figureFooter={
          distribution ? (
            <HealthDistributionBar distribution={distribution} height="sm" />
          ) : undefined
        }
      >
        <p>
          Across{" "}
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {formatNumber(summary.file_count)} files
          </strong>
          , this codebase scores{" "}
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {summary.average_health.toFixed(1)} out of 10
          </strong>{" "}
          on defect risk, weighted by lines of code and built from complexity,
          duplication, coverage, churn and ownership. We rate that{" "}
          {band.label.toLowerCase()}.
          {pillars.length > 0 && (
            <>
              {" "}
              It also scores {pillars.join(" and ")} out of 10;{" "}
              {pillars.length === 1 ? "the two are" : "the three are"} measured
              separately and never blended into one number.
            </>
          )}
        </p>

        {accuracy && (
          <p className="mt-2.5">
            Ranked against real bug-fix history:{" "}
            <strong className="font-semibold text-[var(--color-text-primary)]">
              {accuracy.hits} of the {accuracy.k} files
            </strong>{" "}
            it scores worst were touched by a fix in the last{" "}
            {windowLabel(accuracy.window_days)}. That is{" "}
            {Math.round(accuracy.precision * 100)}% against a{" "}
            {Math.round(accuracy.base_rate * 100)}% base rate across the repo
            {accuracy.lift != null && (
              <>
                , so{" "}
                <strong className="font-semibold text-[var(--color-text-primary)]">
                  {accuracy.lift}× better
                </strong>{" "}
                than picking files at random
              </>
            )}
            .
          </p>
        )}

        {hotspot != null && (
          <p className="mt-2.5">
            The files you change most average{" "}
            <strong className="font-semibold" style={{ color: healthBand(hotspot).color }}>
              {hotspot.toFixed(1)}
            </strong>
            , {describeGap(hotspot, summary.average_health)}
          </p>
        )}
      </PageLede>

      <StatRibbon stats={stats} />
    </div>
  );
}

/**
 * How the hotspot average sits against the repo average, in words.
 *
 * Worth a sentence rather than a delta chip: hotspot health below the repo
 * average is the finding that actually changes what someone does next, and
 * "6.2 (−1.1)" does not say which direction is bad.
 */
function describeGap(hotspot: number, average: number): string {
  const gap = hotspot - average;
  if (Math.abs(gap) < 0.25) return "in line with the codebase overall.";
  return gap < 0
    ? `${Math.abs(gap).toFixed(1)} below the codebase overall. The weak spot is the code in motion.`
    : `${gap.toFixed(1)} above the codebase overall, so the busiest files are holding up.`;
}
