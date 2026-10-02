/**
 * The Code Health page's opening read: one figure, and the sentences that make
 * it mean something.
 *
 * One rather than two. It carried Code health and Maintainability side by side,
 * both scores out of ten, both largely about code shape — so the screen
 * answered "which number do I steer by?" twice, with two different numbers. The
 * page's Counts control answers it instead: the single figure either includes
 * change history or does not, and the reader picks. Maintainability keeps its
 * place in the ribbon, beside the other cuts of the same scoring.
 *
 * Every figure carries an explainer. A number a reader cannot define is a
 * number they cannot act on, and "Performance risk 942" beside "Hotspot health
 * 4.5" reads as two scores when one is a count that only goes up.
 *
 * The prose is not decoration. "329 risks" reads as alarming on its own;
 * "329 static performance risks, scored separately and never blended into the
 * health number" reads as informative. The accuracy claim in particular only
 * means anything next to its base rate: a 72% hit rate is excellent against a
 * 20% baseline and unremarkable against a 70% one, so it is a sentence rather
 * than a badge.
 */

import {
  bandForScore,
  formatScore,
  type DefectAccuracy,
  type HealthDistribution,
  type HealthOverviewSummary,
} from "@repowise-dev/types/health";
import { PageLede } from "../shared/page-lede";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { formatNumber } from "../lib/format";
import { healthBand, healthBandColor, scoreTextColor } from "./tokens";
import { HealthDistributionBar } from "./health-distribution-bar";
import { HEALTH_UNSUPPORTED_NOTICE } from "./map/lens";

const HEALTH_HINT =
  "Fitted against real bug history to predict where defects appear. Built from " +
  "code shape — complexity, duplication, coverage — and from what git says about " +
  "each file: how often it changes, alongside what, and how many people touch it. " +
  "1 to 10, higher is better.";

const CODE_SHAPE_HINT =
  "The same score with its change-history half removed: complexity, " +
  "duplication and coverage, but not churn, co-change, ownership or prior " +
  "fixes. It answers what the code is like rather than what the repository " +
  "has been through, so it moves when you refactor. Not the calibrated " +
  "bug-risk figure — the badge and the leaderboard keep reporting that one.";

const FILES_HINT =
  "Files scored. Only code is scored, so markdown, JSON, YAML, lockfiles and " +
  "other non-code carry no score and are not counted here.";

const MAINTAINABILITY_HINT =
  "Code shape alone: complexity, duplication and error handling, weighted for " +
  "how hard the code is to work with rather than for bug risk. It counts no " +
  "tests and no change history, so it is the figure a refactor moves.";

const PERFORMANCE_HINT =
  "A count of open performance risks, not a score — it counts up as the analyzer " +
  "finds more and falls only when they are fixed. Each one is a database, " +
  "network, filesystem or subprocess call inside a loop, traced across function " +
  "boundaries. Never blended into the health score.";

const HOTSPOT_HINT =
  "Code health averaged over the repo's churn hotspots — the files you change " +
  "most often. Lower than the overall figure on most repos, because " +
  "heavily-changed files carry the most change-history deduction.";

const HOTSPOT_HINT_CODE_SHAPE =
  "Code shape averaged over the repo's churn hotspots — the files you change " +
  "most often. With change history excluded this asks whether the code you " +
  "touch most is well built, rather than how much it has moved.";

/** Which figure the page's current selection describes; marks it in the ribbon. */
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
  /**
   * `secondary` when something else leads the page (Fix first on Code
   * Health): one line with the score and its band, and the prose and the
   * ribbon behind "More". `lead` is the full opening read.
   */
  variant?: "lead" | "secondary";
}

/** "3 months" / "1 month", from a day count. */
function windowLabel(days: number): string {
  const months = Math.max(1, Math.round(days / 30));
  return months === 1 ? "month" : `${months} months`;
}

export function CodeHealthLede(props: CodeHealthLedeProps) {
  const health = props.summary.average_health;
  // No file scored: every file is in a language health has no dialect for.
  // There is no figure to lead with, so the lede says that instead of a 10.
  if (health == null) {
    return (
      <PageLede label="Code health" value="Not analysed" action={props.action}>
        <p>{HEALTH_UNSUPPORTED_NOTICE}</p>
      </PageLede>
    );
  }
  return <ScoredLede {...props} health={health} />;
}

function ScoredLede({
  summary,
  accuracy,
  distribution,
  pillar = "health",
  action,
  variant = "lead",
  health,
}: CodeHealthLedeProps & { health: number }) {
  const unanalysed = summary.unanalysed_file_count ?? 0;
  const maint = summary.maintainability_average;
  const perf = summary.performance_average;
  const perfFindings = summary.performance_findings ?? 0;
  const hotspot = summary.hotspot_health;
  const worstTestPath = summary.worst_test_path;
  const worstTestScore = summary.worst_test_score;
  // Read off the response, not the page's control: the two disagree while a
  // request is in flight, and a figure captioned by the mode the reader just
  // asked for rather than the one it was computed under is the whole bug this
  // page exists to remove.
  const codeShape = summary.counts === "code_shape";
  const unscored = summary.unscored_files ?? 0;
  const structure = summary.structure_average;
  const historyDeduction = summary.history_average;
  // How much of the deduction is git history rather than code. Stated as a
  // share, not as a second score out of 10: a rival 0-10 figure beside
  // Maintainability made the page answer "which number do I steer by?" twice.
  const deduction = (structure ?? 0) + (historyDeduction ?? 0);
  const historyShare =
    structure == null || historyDeduction == null || deduction <= 0
      ? null
      : Math.round((historyDeduction / deduction) * 100);
  // The band words were fitted against the full, bug-predicting score, so
  // they are not a verdict this projection has earned: clamp(10 - structure)
  // reads systematically higher, and almost every repo would print "Excellent"
  // under it. The figure and the spread still say where the repo sits.
  const healthChip = codeShape ? undefined : healthBand(health);

  const stats: RibbonStat[] = [
    { label: "Files", value: formatNumber(summary.file_count), hint: FILES_HINT },
    {
      label: "Maintainability",
      value: maint == null ? "" : formatScore(maint),
      valueColor: maint == null ? undefined : scoreTextColor(maint),
      hint: MAINTAINABILITY_HINT,
      // The map's lens marks its figure here now that the lede carries one
      // number. Dimming the sole headline when the lens moved would have said
      // the page was describing something else.
      highlighted: pillar === "maintainability",
    },
    {
      label: "Performance risk",
      value: perf == null ? "" : formatNumber(perfFindings),
      hint: PERFORMANCE_HINT,
    },
    {
      label: "Hotspot health",
      value: hotspot == null ? "" : formatScore(hotspot),
      valueColor: hotspot == null ? undefined : scoreTextColor(hotspot),
      hint: codeShape ? HOTSPOT_HINT_CODE_SHAPE : HOTSPOT_HINT,
    },
  ];


  const full = (
    <div className="flex flex-col gap-6">
      <PageLede
        label="Code health"
        labelHint={codeShape ? CODE_SHAPE_HINT : HEALTH_HINT}
        value={formatScore(health)}
        valueColor={healthChip?.color}
        unit="out of 10"
        {...(healthChip ? { band: healthChip } : {})}
        action={action}
        layout="beside"
        // The two second reads that belong to this number: how the repo's code
        // volume splits across the bands, and how much of the deduction no
        // refactor can reach. The average alone hides whether this is
        // everything-mediocre or mostly-healthy-with-a-bad-corner, and it hides
        // that a refactor may not move it at all.
        figureFooter={
          <>
            {distribution && <HealthDistributionBar distribution={distribution} height="sm" />}
            {codeShape ? (
              <p className="mt-3 text-[11px] leading-snug text-[var(--color-text-tertiary)]">
                Change history excluded. This scores the code alone, and every
                finding on this page counts toward it.
                {unscored > 0 && (
                  <>
                    {" "}
                    {formatNumber(unscored)} files are not scored here, having no
                    recorded split yet.
                  </>
                )}
              </p>
            ) : (
              historyShare != null && (
                <p className="mt-3 text-[11px] leading-snug text-[var(--color-text-tertiary)]">
                  <span className="tabular-nums text-[var(--color-text-secondary)]">
                    {historyShare}%
                  </span>{" "}
                  of this comes from change history, not from the code. No edit to
                  these files clears it.
                </p>
              )
            )}
          </>
        }
      >
        <p>
          Across{" "}
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {formatNumber(summary.file_count)} files
          </strong>
          , this codebase scores{" "}
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {formatScore(health)} out of 10
          </strong>{" "}
          for code health, weighted by lines of code and built from complexity,
          duplication, coverage
          {codeShape ? "" : ", churn and ownership"}.
          {healthChip ? <> That puts it in the {healthChip.label} band.</> : null}
          {unanalysed > 0 && (
            <>
              {" "}
              {formatNumber(unanalysed)} more {unanalysed === 1 ? "file is" : "files are"} in a
              language health does not analyse yet, so {unanalysed === 1 ? "it is" : "they are"}{" "}
              left out of this figure.
            </>
          )}
          {perf != null && (
            <>
              {" "}
              {/* Not "performance risk": this is a score on the same ladder
                  as the health number, so a risk noun inverts it. "risk" belongs
                  to the findings count in the ribbon. Matches HealthLede. */}
              Static performance is scored separately at {formatScore(perf)} out of
              10 and never blended into the health score.
            </>
          )}
        </p>

        {accuracy && !codeShape && (
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
              {formatScore(hotspot)}
            </strong>
            , {describeGap(hotspot, health)}
          </p>
        )}

        {/* Tests are ranked apart from production files, so the weakest one
            would otherwise never be named on this page. */}
        {worstTestPath && worstTestScore != null && (
          <p className="mt-2.5">
            Test files are ranked apart. The lowest scoring is{" "}
            <span className="break-all font-mono text-[12px] text-[var(--color-text-primary)]">
              {worstTestPath}
            </span>{" "}
            at{" "}
            <strong
              className="font-semibold"
              style={{ color: healthBandColor(bandForScore(worstTestScore)) }}
            >
              {formatScore(worstTestScore)}
            </strong>
            .
          </p>
        )}
      </PageLede>

      <StatRibbon stats={stats} />
    </div>
  );

  if (variant === "lead") return full;

  return (
    <details className="group">
      <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-2.5 gap-y-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] [&::-webkit-details-marker]:hidden">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          Code health
        </span>
        <span
          className="text-[15px] font-semibold tabular-nums text-[var(--color-text-primary)]"
          {...(healthChip ? { style: { color: healthChip.color } } : {})}
        >
          {formatScore(health)}
        </span>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          out of 10 across {formatNumber(summary.file_count)} files
        </span>
        {healthChip ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: healthChip.color }}
            />
            {healthChip.label}
          </span>
        ) : null}
        <span className="text-xs font-medium text-[var(--color-accent-primary)] group-open:hidden">
          More
        </span>
        <span className="hidden text-xs font-medium text-[var(--color-accent-primary)] group-open:inline">
          Less
        </span>
      </summary>
      <div className="mt-5">{full}</div>
    </details>
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
