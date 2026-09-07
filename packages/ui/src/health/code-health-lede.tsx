/**
 * The Code Health page's opening read: the two figures that lead, and the
 * sentences that make them mean something.
 *
 * Two rather than one because they answer different questions. Code health is
 * the calibrated, bug-predicting number, and roughly half of what it deducts
 * comes from git history, so it can fall through a week of good refactoring
 * and tell the reader nothing they can act on. Maintainability is pure code
 * shape, which is why it sits at the same weight rather than in the ribbon: it
 * is the number a refactor is supposed to move.
 *
 * The prose is not decoration. "329 risks" reads as alarming on its own;
 * "329 static performance risks, scored separately and never blended into the
 * health number" reads as informative. Same figure. The accuracy claim in
 * particular only means anything next to its base rate: a 72% hit rate is
 * excellent against a 20% baseline and unremarkable against a 70% one, so it
 * is a sentence rather than a badge.
 */

import {
  bandForScore,
  HEALTH_BAND_LABEL,
  type DefectAccuracy,
  type HealthDistribution,
  type HealthOverviewSummary,
} from "@repowise-dev/types/health";
import { LedeFigure, PageLede } from "../shared/page-lede";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { formatNumber } from "../lib/format";
import { healthBandColor, scoreTextColor } from "./tokens";
import { HealthDistributionBar } from "./health-distribution-bar";

/** Which of the two figures the page's current selection describes. */
export type LedePillar = "health" | "maintainability";

export interface CodeHealthLedeProps {
  summary: HealthOverviewSummary;
  /** Null when the repo lacks the defect history to make an honest claim. */
  accuracy?: DefectAccuracy | null;
  /** NLOC-weighted split across the bands, shown under the score. */
  distribution?: HealthDistribution | null;
  /**
   * The figure the map is currently coloured by. It highlights one of the two
   * and never changes either value: a lens is a way of looking at the repo,
   * not a different repo.
   */
  pillar?: LedePillar;
  /** Rendered under the prose, for the host's pillar deep-links. */
  action?: React.ReactNode;
}

/** "3 months" / "1 month", from a day count. */
function windowLabel(days: number): string {
  const months = Math.max(1, Math.round(days / 30));
  return months === 1 ? "month" : `${months} months`;
}

/** A score as a canonical three-band chip. */
function bandChip(score: number): { label: string; color: string } {
  const band = bandForScore(score);
  return { label: HEALTH_BAND_LABEL[band], color: healthBandColor(band) };
}

export function CodeHealthLede({
  summary,
  accuracy,
  distribution,
  pillar = "health",
  action,
}: CodeHealthLedeProps) {
  const health = summary.average_health;
  const maint = summary.maintainability_average;
  const perf = summary.performance_average;
  const perfFindings = summary.performance_findings ?? 0;
  const hotspot = summary.hotspot_health;
  const structure = summary.structure_average;
  const healthChip = bandChip(health);

  const stats: RibbonStat[] = [
    { label: "Files", value: formatNumber(summary.file_count) },
    {
      label: "Performance risk",
      value: perf == null ? "" : formatNumber(perfFindings),
      hint: "Open static performance risks: a DB, network, filesystem or subprocess call per loop iteration, found across function boundaries. High precision, low recall. This is a count of open causes, not a score, so it counts up as the analyzer finds more and falls only when they are fixed.",
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
        label="Code health"
        value={health.toFixed(1)}
        valueColor={healthChip.color}
        unit="out of 10"
        band={healthChip}
        action={action}
        layout="beside"
        figureHighlighted={pillar === "health"}
        // The two second reads that belong to this number: how the repo's code
        // volume splits across the bands, and which half of the score is
        // holding it down. The average alone hides whether this is
        // everything-mediocre or mostly-healthy-with-a-bad-corner, and it hides
        // that a refactor may not move it at all.
        figureFooter={
          <>
            {distribution && <HealthDistributionBar distribution={distribution} height="sm" />}
            {structure != null && (
              <p className="mt-3 text-[11px] leading-snug text-[var(--color-text-tertiary)]">
                Structure{" "}
                <span className="tabular-nums text-[var(--color-text-secondary)]">
                  {(10 - structure).toFixed(1)}
                </span>
                . History pulls it to{" "}
                <span className="tabular-nums text-[var(--color-text-secondary)]">
                  {health.toFixed(1)}
                </span>
                .
              </p>
            )}
          </>
        }
        figureSecondary={
          <LedeFigure
            label="Maintainability"
            value={maint == null ? "—" : maint.toFixed(1)}
            valueColor={maint == null ? undefined : bandChip(maint).color}
            unit={maint == null ? undefined : "out of 10"}
            band={maint == null ? undefined : bandChip(maint)}
            highlighted={pillar === "maintainability"}
            footer={
              <p className="text-[11px] leading-snug text-[var(--color-text-tertiary)]">
                {maint == null
                  ? "Not measured on this index."
                  : "Code shape only. This is the number that moves when you refactor."}
              </p>
            }
          />
        }
      >
        <p>
          Across{" "}
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {formatNumber(summary.file_count)} files
          </strong>
          , this codebase scores{" "}
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {health.toFixed(1)} out of 10
          </strong>{" "}
          for code health, weighted by lines of code and built from complexity,
          duplication, coverage, churn and ownership. We rate that{" "}
          {healthChip.label.toLowerCase()}.
          {perf != null && (
            <>
              {" "}
              Static performance risk is scored separately at {perf.toFixed(1)} out
              of 10 and never blended into either figure.
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
            <strong
              className="font-semibold"
              style={{ color: healthBandColor(bandForScore(hotspot)) }}
            >
              {hotspot.toFixed(1)}
            </strong>
            , {describeGap(hotspot, health)}
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
 * "6.2 (-1.1)" does not say which direction is bad.
 */
function describeGap(hotspot: number, average: number): string {
  const gap = hotspot - average;
  if (Math.abs(gap) < 0.25) return "in line with the codebase overall.";
  return gap < 0
    ? `${Math.abs(gap).toFixed(1)} below the codebase overall. The weak spot is the code in motion.`
    : `${gap.toFixed(1)} above the codebase overall, so the busiest files are holding up.`;
}
