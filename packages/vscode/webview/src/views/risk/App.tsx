/**
 * Change risk: the diff-shape score for the committed range as the lede, a
 * verdict strip, what the working change touches, and the statistical
 * breakdown.
 *
 * Grouping is hairlines and vertical rhythm, not boxes. This file carried
 * thirty `Card` wrappers and not one of them was clickable — a card means "a
 * discrete object you can act on", and a driver bar, a feature count and a
 * reviewer ranking are none of those. Everything that does act (the verdict
 * chips, the file rows, Copy, Run again) keeps its affordance.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Copy,
  Gauge,
  GitCompare,
  GitPullRequest,
  Network,
  RotateCw,
  ShieldCheck,
  TestTube,
  Users,
} from "lucide-react";
import { Badge, Button } from "@repowise-dev/ui/ui";
import { EmptyState } from "@repowise-dev/ui/shared";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { OverviewSection } from "@repowise-dev/ui/overview";
import type { ViewProps } from "../../runtime/mount";
import type { WebviewHost } from "../../runtime/rpc";
import type {
  ChangeImpactReport,
  RiskRangeReport,
} from "../../../../src/shared/webviewMessages";
import { selectMissingCochanges } from "../../../../src/shared/changeImpact";
import { selectDirectRisks, type RankedDirectRisk } from "./selectors";

/** Human labels for the raw change features the endpoint returns, in report order. */
const FEATURE_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["la", "Lines added"],
  ["ld", "Lines deleted"],
  ["nf", "Files changed"],
  ["nd", "Directories changed"],
  ["ns", "Subsystems changed"],
  ["entropy", "Change entropy"],
  ["exp", "Author experience"],
];

type Tone = "low" | "medium" | "high";

/** Maps the server's authoritative absolute fallback band to presentation. */
function fallbackBandTone(band: string | null): Tone {
  if (band === "high") return "high";
  if (band === "moderate") return "medium";
  return "low";
}

const TONE_VAR: Record<Tone, string> = {
  low: "var(--color-risk-low)",
  medium: "var(--color-risk-medium)",
  high: "var(--color-risk-high)",
};

/** Fallback co-change floor when settings cannot be read; mirrors the
 *  changeIntel.cochangeMinScore default. */
const COCHANGE_MIN_SCORE = 4;

/** Anchors the verdict chips scroll to; each id sits on its impact card. */
const SECTION_IDS = {
  directRisks: "impact-direct-risks",
  downstream: "impact-downstream",
  cochanges: "impact-cochanges",
  testGaps: "impact-test-gaps",
} as const;

function scrollToSection(id: string) {
  document
    .getElementById(id)
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** Formats a contribution with an explicit sign (positive raises the model score). */
function signed(value: number): string {
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(2)}`;
}

function formatFeatureValue(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/** "87th", "91st", "22nd". The percentile reads in a sentence now, where a
 *  hardcoded "th" produces "91th". */
function ordinal(n: number): string {
  const v = Math.round(n);
  if (v % 100 >= 11 && v % 100 <= 13) return `${v}th`;
  return `${v}${["th", "st", "nd", "rd"][v % 10] ?? "th"}`;
}

export function App({ host, repo, refreshToken }: ViewProps<"risk">) {
  const [report, setReport] = useState<RiskRangeReport | null>(null);
  const [impact, setImpact] = useState<ChangeImpactReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [impactLoading, setImpactLoading] = useState(true);
  const [cochangeFloor, setCochangeFloor] = useState(COCHANGE_MIN_SCORE);

  const load = useCallback(() => {
    setLoading(true);
    setImpactLoading(true);
    setError(null);
    // Keep the panel's co-change floor in step with the nudge's setting.
    host.api
      .getSettings()
      .then((s) => {
        const floor = s["changeIntel.cochangeMinScore"];
        if (typeof floor === "number") setCochangeFloor(floor);
      })
      .catch(() => undefined);
    host.api
      .riskRange()
      .then((r) => setReport(r))
      .catch((err: unknown) =>
        setError(
          err instanceof Error ? err.message : "Could not score change risk.",
        ),
      )
      .finally(() => setLoading(false));
    // Structural impact is independent: a git-less workspace still shows it.
    host.api
      .changeImpact()
      .then((r) => setImpact(r))
      .catch(() => setImpact(null))
      .finally(() => setImpactLoading(false));
  }, [host]);

  // Refetch on mount and whenever the index moves under the panel. Both scopes
  // reflect the working tree, so there is no cache to reuse here.
  useEffect(() => {
    load();
  }, [load, refreshToken]);

  const branch = report?.branch ?? repo.defaultBranch ?? "HEAD";
  const base = report?.base ?? "";

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 p-6 sm:gap-8">
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold tracking-tight text-[var(--color-text-primary)]">
            Change risk
          </h1>
          <p className="mt-1 flex items-center gap-2 text-[15px] text-[var(--color-text-secondary)]">
            <GitCompare className="h-4 w-4 shrink-0" />
            <span className="truncate">
              <code className="text-[var(--color-text-primary)]">{branch}</code>
              <span className="mx-1.5 text-[var(--color-text-tertiary)]">
                vs
              </span>
              <code className="text-[var(--color-text-primary)]">
                {base || "base"}
              </code>
            </span>
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RotateCw className={loading ? "animate-spin" : undefined} />
          Run again
        </Button>
      </header>

      {loading && !report ? (
        <RiskSkeleton base={base} />
      ) : error ? (
        <EmptyState
          icon={<ShieldCheck className="h-8 w-8" />}
          title="Could not score change risk"
          description={error}
          action={{ label: "Try again", onClick: load }}
        />
      ) : report ? (
        <>
          <ScoreHero report={report} />
          <VerdictStrip impact={impact} cochangeFloor={cochangeFloor} />
          <ChangeImpact
            impact={impact}
            loading={impactLoading}
            host={host}
            cochangeFloor={cochangeFloor}
          />
          <RiskBreakdown report={report} />
        </>
      ) : null}
    </div>
  );
}

/** Skeleton matching the report layout (score hero + driver bars) so the panel
 *  holds its shape while the working tree is scored. */
function RiskSkeleton({ base }: { base: string }) {
  return (
    <div className="flex flex-col gap-6 sm:gap-8" aria-hidden>
      <div className="flex flex-col gap-4 sm:flex-row sm:gap-8">
        <div className="flex shrink-0 flex-col gap-2.5">
          <div className="h-3 w-20 animate-pulse rounded bg-[var(--color-bg-inset)]" />
          <div className="h-12 w-32 animate-pulse rounded bg-[var(--color-bg-inset)]" />
        </div>
        <div className="min-w-0 flex-1 space-y-2 pt-1">
          <div className="h-4 w-full animate-pulse rounded bg-[var(--color-bg-inset)]" />
          <div className="h-4 w-4/5 animate-pulse rounded bg-[var(--color-bg-inset)]" />
        </div>
      </div>
      <div className="flex flex-col gap-3 border-t border-[var(--color-border-default)] pt-6 sm:pt-8">
        <div className="h-5 w-44 animate-pulse rounded bg-[var(--color-bg-inset)]" />
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3">
            <div className="h-3 w-40 shrink-0 animate-pulse rounded bg-[var(--color-bg-inset)]" />
            <div className="h-2 flex-1 animate-pulse rounded-full bg-[var(--color-bg-inset)]" />
            <div className="h-3 w-14 shrink-0 animate-pulse rounded bg-[var(--color-bg-inset)]" />
          </div>
        ))}
      </div>
      <p className="flex items-center justify-center gap-2 text-xs text-[var(--color-text-tertiary)]">
        <RotateCw className="h-3.5 w-3.5 animate-spin" />
        Scoring the working tree{base ? ` against ${base}` : ""}
      </p>
    </div>
  );
}

/** Repo-relative review priority mapped onto the three risk tones. */
const PRIORITY_TONE: Record<string, Tone> = {
  low: "low",
  moderate: "medium",
  high: "high",
};

/**
 * Where this range sits in its own repo's risk distribution.
 *
 * The lede is the ranking, not the raw score. That 0-10 figure is calibrated
 * on single commits, so a range spanning several reads high by construction
 * and says more about the corpus than about this change; it stays on the page
 * as a secondary, explicitly anchored line.
 */
function ScoreHero({ report }: { report: RiskRangeReport }) {
  const r = report.result;
  const priority = r.review_priority;
  const percentile = r.risk_percentile;
  const ranked = percentile != null && priority != null;
  const color = ranked
    ? TONE_VAR[PRIORITY_TONE[priority] ?? "medium"]
    : TONE_VAR[fallbackBandTone(r.fallback_band)];
  return (
    <PageLede
      // Not "Change risk" again: the page's h1 two lines above already says
      // that, and a micro-label repeating the heading verbatim labels nothing.
      label={ranked ? "Review priority" : "Diff shape"}
      value={ranked ? (r.classification ?? priority) : r.score.toFixed(1)}
      valueColor={color}
      unit={ranked ? "for this repo" : "out of 10"}
      band={
        ranked
          ? { label: `${ordinal(percentile)} percentile`, color }
          : { label: `${r.fallback_band ?? "unranked"} absolute per-commit band`, color }
      }
      layout="beside"
      badge={
        r.is_fix ? (
          <Badge variant="outline" title="Classified as a fix change">
            fix
          </Badge>
        ) : undefined
      }
    >
      <p>
        Scored from the shape of this range's diff — how much it adds and
        deletes, how many files, directories and subsystems it spreads across,
        and how experienced its author is in them.{" "}
        {ranked ? (
          <>
            That ranks it above{" "}
            <strong className="font-semibold text-[var(--color-text-primary)] tabular-nums">
              {Math.round(percentile)}%
            </strong>{" "}
            of recent commits in this repository.
          </>
        ) : (
          <>There was no baseline of recent commits to rank it against.</>
        )}
      </p>
      <FixHistoryNote history={r.fix_history} />
      <p className="mt-2.5 text-[var(--color-text-tertiary)]">
        Diff-size score{" "}
        <strong className="font-semibold tabular-nums">
          {r.score.toFixed(1)}/10
        </strong>{" "}
        — how big and spread out the change is, not where it lands. Anchored to
        a corpus of single commits, so a range reads high by construction.
      </p>
    </PageLede>
  );
}

/**
 * The bug-fix record of the files this range touches — the part the diff-size
 * score cannot see. Renders nothing when no touched file has fix history, which
 * is itself worth not padding out.
 */
function FixHistoryNote({
  history,
}: {
  history: RiskRangeReport["result"]["fix_history"];
}) {
  if (!history.available) {
    return (
      <p className="mt-2.5">
        Fix history unavailable — the git history walk failed.
      </p>
    );
  }
  if (!history.files.length) return null;
  const [worst] = history.files;
  return (
    <p className="mt-2.5">
      These files have broken before:{" "}
      <strong className="font-semibold text-[var(--color-text-primary)]">
        {worst.path}
      </strong>{" "}
      has{" "}
      <strong className="font-semibold tabular-nums">
        {worst.fix_pressure.toFixed(1)}
      </strong>{" "}
      recency-weighted prior fixes
      {history.files.length > 1
        ? `, and ${history.files.length - 1} more do too`
        : ""}
      {history.percentile != null ? (
        <>
          {" "}
          — {ordinal(history.percentile)} percentile of this repo&apos;s recent
          commits
        </>
      ) : null}
      .
    </p>
  );
}

/** Quiet one-line summary of the blast data. Each chip anchors its section;
 *  zero counts render no chip, and a clean tree renders nothing at all. */
function VerdictStrip({
  impact,
  cochangeFloor,
}: {
  impact: ChangeImpactReport | null;
  cochangeFloor: number;
}) {
  const blast = impact?.blast;
  // `changed.length === 0` matters as well as `blast`: the impact block below
  // renders an empty state in that case, so a chip would scroll to an anchor
  // that is not on the page. The two must bail on the same condition.
  if (!impact || !blast || impact.gitUnavailable || impact.changed.length === 0)
    return null;

  const downstream = blast.transitive_affected.length;
  const cochanges = selectMissingCochanges(impact, cochangeFloor).length;
  const gaps = blast.test_gaps.length;
  const testImpact = blast.test_impact;
  const testAnalysisUnavailable =
    !testImpact ||
    ["unavailable", "degraded"].includes(testImpact.coverage.status);
  const testAnalysisLimited =
    testAnalysisUnavailable ||
    Boolean(testImpact?.analysis.partial || testImpact?.analysis.stale);

  const chips: Array<{ id: string; label: string }> = [];
  if (downstream > 0) {
    chips.push({
      id: SECTION_IDS.downstream,
      label: `may affect ${downstream} downstream file${downstream === 1 ? "" : "s"}`,
    });
  }
  if (cochanges > 0) {
    chips.push({
      id: SECTION_IDS.cochanges,
      label: `${cochanges} co-change partner${cochanges === 1 ? "" : "s"} untouched`,
    });
  }
  if (testAnalysisUnavailable) {
    chips.push({
      id: SECTION_IDS.testGaps,
      label: "test analysis unavailable",
    });
  } else if (testImpact.analysis.stale || testImpact.analysis.partial) {
    chips.push({
      id: SECTION_IDS.testGaps,
      label: testImpact.analysis.stale
        ? "test evidence stale"
        : "test analysis partial",
    });
  }
  if (testImpact && testImpact.recommendations_total > 0) {
    chips.push({
      id: SECTION_IDS.testGaps,
      label: `${testImpact.recommendations_total} test recommendation${testImpact.recommendations_total === 1 ? "" : "s"}`,
    });
  } else if (!testAnalysisLimited && gaps > 0) {
    chips.push({
      id: SECTION_IDS.testGaps,
      label:
        gaps === 1
          ? "no test evidence found for 1 changed file"
          : `no test evidence found for ${gaps} changed files`,
    });
  }
  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {chips.map((c) => (
        <button
          key={`${c.id}:${c.label}`}
          type="button"
          onClick={() => scrollToSection(c.id)}
          className="inline-flex items-center rounded-full border border-[var(--color-border-default)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-surface)]"
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}

/** The statistical breakdown: what moves the score, and the raw change shape. */
function RiskBreakdown({ report }: { report: RiskRangeReport }) {
  const r = report.result;
  const drivers = [...r.drivers].sort(
    (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution),
  );
  const maxDriver = drivers.reduce(
    (m, d) => Math.max(m, Math.abs(d.contribution)),
    0,
  );
  const featureRows = FEATURE_LABELS.filter(
    ([key]) => r.features[key] != null,
  ) as ReadonlyArray<readonly [string, string]>;

  return (
    <>
      {drivers.length > 0 && (
        <OverviewSection
          title="What moves the score"
          description="The signed contribution of each feature that explains the score. Positive raises the estimate, negative lowers it; the bar is relative to the strongest driver. File, directory and subsystem counts enter the score but are not shown, because their fitted signs are collinearity with diff size rather than a finding."
        >
          <div className="flex flex-col gap-3">
            {drivers.map((d) => {
              const raises = d.contribution >= 0;
              const barColor = raises
                ? "var(--color-error)"
                : "var(--color-success)";
              const pct =
                maxDriver > 0
                  ? (Math.abs(d.contribution) / maxDriver) * 100
                  : 0;
              return (
                <div
                  key={d.feature}
                  className="flex items-center gap-3 text-[15px]"
                >
                  <span className="w-40 shrink-0 truncate text-[var(--color-text-secondary)]">
                    {d.label}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--color-bg-elevated)]">
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${pct}%`, backgroundColor: barColor }}
                    />
                  </div>
                  <span
                    className="w-14 shrink-0 text-right font-medium tabular-nums"
                    style={{ color: barColor }}
                  >
                    {signed(d.contribution)}
                  </span>
                </div>
              );
            })}
          </div>
        </OverviewSection>
      )}

      {featureRows.length > 0 && (
        <OverviewSection
          title="Change shape"
          description="The raw diff measurements the score is computed from."
        >
          <dl className="grid grid-cols-1 gap-x-8 sm:grid-cols-2">
            {featureRows.map(([key, label]) => (
              <div
                key={key}
                className="flex items-baseline justify-between gap-3 border-b border-[var(--color-border-default)] py-2 last:border-b-0"
              >
                <dt className="text-[15px] text-[var(--color-text-secondary)]">
                  {label}
                </dt>
                <dd className="text-[15px] font-medium tabular-nums text-[var(--color-text-primary)]">
                  {formatFeatureValue(r.features[key] as number)}
                </dd>
              </div>
            ))}
          </dl>
        </OverviewSection>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Change impact (blast radius + reviewers for the working change set)
// ---------------------------------------------------------------------------

function ChangeImpact({
  impact,
  loading,
  host,
  cochangeFloor,
}: {
  impact: ChangeImpactReport | null;
  loading: boolean;
  host: WebviewHost;
  cochangeFloor: number;
}) {
  if (loading && !impact) {
    return (
      <div
        className="flex flex-col gap-3 border-t border-[var(--color-border-default)] pt-6 sm:pt-8"
        aria-hidden
      >
        <div className="h-5 w-48 animate-pulse rounded bg-[var(--color-bg-inset)]" />
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex items-center gap-2">
            <div className="h-3 flex-1 animate-pulse rounded bg-[var(--color-bg-inset)]" />
            <div className="h-1.5 w-16 shrink-0 animate-pulse rounded-full bg-[var(--color-bg-inset)]" />
          </div>
        ))}
      </div>
    );
  }
  if (!impact) return null;

  if (impact.gitUnavailable) {
    return (
      <p className="border-t border-[var(--color-border-default)] pt-6 text-[15px] text-[var(--color-text-tertiary)] sm:pt-8">
        Enable Git for this workspace to see what your change touches, who
        usually changes it with you, and who could review it.
      </p>
    );
  }

  if (impact.changed.length === 0) {
    return (
      <EmptyState
        icon={<Network className="h-8 w-8" />}
        title="No pending changes"
        description="There are no uncommitted or unpushed changes to analyze. Impact appears here as soon as you edit or commit."
      />
    );
  }

  const blast = impact.blast;
  const directRisks = selectDirectRisks(impact);
  const downstream = blast?.transitive_affected ?? [];
  const cochanges = selectMissingCochanges(impact, cochangeFloor);
  const testGaps = blast?.test_gaps ?? [];
  const testImpact = blast?.test_impact;
  const testRecommendations = testImpact?.recommendations ?? [];
  const testAnalysisUnavailable =
    !testImpact ||
    ["unavailable", "degraded"].includes(testImpact.coverage.status);
  const testAnalysisLimited =
    testAnalysisUnavailable ||
    Boolean(testImpact?.analysis.partial || testImpact?.analysis.stale);
  const testAnalysisHint = !testImpact
    ? "This older server did not provide typed test-analysis state; an empty list does not mean no tests are needed."
    : testImpact.analysis.stale
      ? "Coverage evidence is stale; measured recommendations may not describe the indexed commit."
      : testImpact.analysis.degraded
        ? "Test analysis is degraded; an empty list does not mean no tests are needed."
        : testImpact.analysis.partial
          ? "Test analysis is partial; recommendations may not cover every evidence input."
          : "Recommendations keep measured coverage evidence separate from structural inference.";
  const reviewers = impact.reviewers;
  const structuralImpact = blast?.structural_impact_score ?? null;

  const scopeLabel =
    impact.scope === "branch" ? "uncommitted and unpushed" : "uncommitted";

  return (
    <OverviewSection
      title="What this change touches"
      action={
        <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {impact.changed.length} {scopeLabel} file
          {impact.changed.length === 1 ? "" : "s"}
          {structuralImpact != null && (
            <>
              {" · "}
              <span className="font-medium text-[var(--color-text-secondary)]">
                structural impact {structuralImpact.toFixed(1)}/10 (
                {blast?.structural_impact_band}; heuristic)
              </span>
            </>
          )}
        </span>
      }
      className="gap-6"
    >
      {directRisks.length > 0 && (
        <ImpactBlock
          id={SECTION_IDS.directRisks}
          icon={<Gauge className="h-4 w-4" />}
          title="Highest structural weight in this change"
          hint="Relative pagerank-weighted hotspot heuristic. It is not a breakage probability."
        >
          {directRisks.slice(0, 10).map((f) => (
            <DirectRiskRow
              key={f.path}
              risk={f}
              onOpen={() => host.openFile(f.path)}
            />
          ))}
          <MoreRow count={directRisks.length - 10} />
        </ImpactBlock>
      )}

      {downstream.length > 0 && (
        <ImpactBlock
          id={SECTION_IDS.downstream}
          icon={<Network className="h-4 w-4" />}
          title="Downstream of your changes"
          hint="These files depend on what you changed. Verify they still work."
        >
          {downstream.slice(0, 10).map((t) => (
            <PathRow
              key={t.path}
              path={t.path}
              trailing={`depth ${t.depth}`}
              onOpen={() => host.openFile(t.path)}
            />
          ))}
          <MoreRow count={downstream.length - 10} />
        </ImpactBlock>
      )}

      {cochanges.length > 0 && (
        <ImpactBlock
          id={SECTION_IDS.cochanges}
          icon={<GitPullRequest className="h-4 w-4" />}
          title="Usually changes together"
          hint="History suggests these often change with your edits. Advisory, not a rule."
        >
          {cochanges.slice(0, 8).map((c) => (
            <PathRow
              key={c.partner}
              path={c.partner}
              trailing={`${c.score}×`}
              onOpen={() => host.openFile(c.partner)}
            />
          ))}
          <MoreRow count={cochanges.length - 8} />
        </ImpactBlock>
      )}

      {(testGaps.length > 0 ||
        testRecommendations.length > 0 ||
        testAnalysisLimited) && (
        <ImpactBlock
          id={SECTION_IDS.testGaps}
          icon={<TestTube className="h-4 w-4" />}
          title="Test impact"
          hint={testAnalysisHint}
        >
          {testRecommendations.slice(0, 10).map((recommendation) => {
            const path =
              recommendation.test_file ?? recommendation.test_id.split("::")[0];
            return (
              <PathRow
                key={`${recommendation.repository_id}:${recommendation.test_id}`}
                path={path}
                trailing={`${recommendation.basis} · ${recommendation.repository}`}
                onOpen={() => host.openFile(path)}
              />
            );
          })}
          <MoreRow
            count={
              (testImpact?.recommendations_total ??
                testRecommendations.length) -
              Math.min(testRecommendations.length, 10)
            }
          />
          {testGaps.slice(0, 8).map((p) => (
            <PathRow
              key={p}
              path={p}
              trailing="no test evidence"
              onOpen={() => host.openFile(p)}
            />
          ))}
          <MoreRow count={testGaps.length - 8} />
        </ImpactBlock>
      )}

      {reviewers.length > 0 && <Reviewers reviewers={reviewers} host={host} />}
    </OverviewSection>
  );
}

/** One block of the impact read. A hairline and a heading, not a card: the
 *  rows inside are the things you can act on, and each carrying its own box
 *  put four bordered containers around four lists of file names. */
function ImpactBlock({
  id,
  icon,
  title,
  hint,
  children,
}: {
  /** Anchor for the verdict chips; omitted for blocks without a chip. */
  id?: string;
  icon: React.ReactNode;
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section
      {...(id ? { id } : {})}
      className="flex scroll-mt-6 flex-col gap-1.5 border-t border-[var(--color-border-default)] pt-4"
    >
      <h3 className="flex items-center gap-2 text-[15px] font-semibold text-[var(--color-text-primary)]">
        <span className="text-[var(--color-text-tertiary)]">{icon}</span>
        {title}
      </h3>
      <p className="text-xs text-[var(--color-text-tertiary)]">{hint}</p>
      <div className="mt-1 flex flex-col gap-0.5">{children}</div>
    </section>
  );
}

function PathRow({
  path,
  trailing,
  onOpen,
}: {
  path: string;
  trailing?: string;
  onOpen: () => void;
}) {
  const name = path.split("/").pop() || path;
  const dir = path.slice(0, path.length - name.length);
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`Open ${path}`}
      className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-[15px] transition-colors hover:bg-[var(--color-bg-surface)]"
    >
      <span className="min-w-0 flex-1 truncate">
        {dir && (
          <span className="text-[var(--color-text-tertiary)]">{dir}</span>
        )}
        <span className="text-[var(--color-text-primary)]">{name}</span>
      </span>
      {trailing && (
        <span className="shrink-0 text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {trailing}
        </span>
      )}
    </button>
  );
}

/** A clickable file row with a quiet relative risk bar and a hotspot marker. */
function DirectRiskRow({
  risk,
  onOpen,
}: {
  risk: RankedDirectRisk;
  onOpen: () => void;
}) {
  const name = risk.path.split("/").pop() || risk.path;
  const dir = risk.path.slice(0, risk.path.length - name.length);
  const pct = Math.min(100, Math.max(0, risk.share * 100));
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`Open ${risk.path}`}
      className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-[15px] transition-colors hover:bg-[var(--color-bg-surface)]"
    >
      <span className="min-w-0 flex-1 truncate">
        {dir && (
          <span className="text-[var(--color-text-tertiary)]">{dir}</span>
        )}
        <span className="text-[var(--color-text-primary)]">{name}</span>
      </span>
      {risk.hotspot && (
        <span
          className="shrink-0 text-xs text-[var(--color-text-tertiary)]"
          title="Changes often in recent history"
        >
          hotspot
        </span>
      )}
      <span
        className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-[var(--color-bg-elevated)]"
        title="Structural weight relative to the strongest file in this change"
      >
        <span
          className="block h-full rounded-full"
          style={{
            width: `${pct}%`,
            backgroundColor:
              "color-mix(in srgb, var(--color-text-secondary) 45%, transparent)",
          }}
        />
      </span>
    </button>
  );
}

function MoreRow({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <p className="px-1.5 pt-1 text-xs text-[var(--color-text-tertiary)]">
      +{count} more
    </p>
  );
}

function Reviewers({
  reviewers,
  host,
}: {
  reviewers: ChangeImpactReport["reviewers"];
  host: WebviewHost;
}) {
  const top = reviewers.slice(0, 5);
  const copy = () => {
    const text = top
      .map((r) => (r.email ? `${r.name} <${r.email}>` : r.name))
      .join(", ");
    host.copyText(
      `Suggested reviewers: ${text}`,
      "Reviewers copied to clipboard.",
    );
  };
  const maxScore = top.reduce((m, r) => Math.max(m, r.score), 0);

  return (
    <section className="flex flex-col gap-1.5 border-t border-[var(--color-border-default)] pt-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-[15px] font-semibold text-[var(--color-text-primary)]">
            <span className="text-[var(--color-text-tertiary)]">
              <Users className="h-4 w-4" />
            </span>
            Suggested reviewers
          </h3>
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Ranked by ownership and co-change history of the changed files.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={copy} className="shrink-0">
          <Copy className="h-3.5 w-3.5" />
          Copy
        </Button>
      </div>
      <div className="mt-1 flex flex-col gap-2.5">
        {top.map((r) => {
          const pct = maxScore > 0 ? (r.score / maxScore) * 100 : 0;
          return (
            <div key={r.email ?? r.name} className="space-y-1">
              <div className="flex items-center justify-between gap-3 text-[15px]">
                <span className="min-w-0 truncate font-medium text-[var(--color-text-primary)]">
                  {r.name}
                </span>
                <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
                  {r.recent_commits} recent commit
                  {r.recent_commits === 1 ? "" : "s"}
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-bg-elevated)]">
                <div
                  className="h-full rounded-full bg-[var(--color-accent-primary)]"
                  style={{ width: `${pct}%` }}
                />
              </div>
              {r.reasons.length > 0 && (
                <p className="truncate text-xs text-[var(--color-text-tertiary)]">
                  {r.reasons.join(" · ")}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
