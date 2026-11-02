"use client";

/**
 * The file drawer's collapsed sections below the fold: Details (the metric
 * grid), History & ownership, Dependencies, Elsewhere for this file, and Why
 * this score. Each hint names what is inside without restating its numbers.
 */

import { CollapsibleSection } from "../../shared/collapsible-section";
import { ApiError } from "../../shared/api-error";
import { InfoTip } from "../../shared/info-tip";
import { toFriendlyMessage } from "../../lib/errors";
import { bandForScore, formatScore } from "@repowise-dev/types/health";
import type { FileSignals } from "@repowise-dev/types/health";
import { FileSignalsPanel, hasAnySignal } from "../file-signals-panel";
import { RelatedWork, type RelatedLensName, type RelatedWorkSlotProps } from "../related-work";
import { ScoreBreakdown, type ScoreBreakdownCategory } from "../score-breakdown";
import { healthBandColor } from "../tokens";
import type { HealthDrawerMetric, HealthFileDrawerProps } from "../health-file-drawer";
import { MICRO_LABEL } from "./shared";

export function DetailsSection({ metric }: { metric: HealthDrawerMetric }) {
  if (metric.score == null && metric.nloc == null) return null;
  return (
    <CollapsibleSection title="Details" hint="size, complexity, tests, module">
      <MetricGrid metric={metric} />
    </CollapsibleSection>
  );
}

/**
 * Process and people signals plus where the bug fixes landed, as one section:
 * the fix count and its recency live in the process row, so the per-symbol
 * list under it does not repeat them.
 *
 * The per-symbol counts are labelled approximate, because symbol spans are
 * current-tree while each fix's line ranges are numbered on its own parent
 * commit. No commit is named: file-level SZZ ran at 74.5% precision, enough to
 * count fixes and not enough to say which commit caused one.
 */
export function HistorySection({ signals }: { signals: FileSignals | null | undefined }) {
  if (!signals) return null;
  const counts = signals.fix_symbol_counts;
  const hasFixes = Boolean(counts && Object.keys(counts).length > 0);
  if (!hasFixes && !hasAnySignal(signals, ["process", "people"])) return null;
  const entries = hasFixes ? Object.entries(counts!).sort((a, b) => b[1] - a[1]) : [];

  return (
    <CollapsibleSection title="History & ownership" hint={historyHint(signals)}>
      <FileSignalsPanel signals={signals} groups={["process", "people"]} hideHeading />
      {hasFixes ? (
        <div className="flex flex-col gap-1.5 border-t border-[var(--color-border-default)] pt-2.5">
          <p className={MICRO_LABEL}>Where the fixes landed</p>
          <ul className="space-y-1">
            {entries.map(([symbolId, count]) => (
              <li
                key={symbolId}
                className="flex items-baseline gap-2 text-xs text-[var(--color-text-secondary)]"
              >
                <code className="font-mono text-[var(--color-text-primary)]">
                  {symbolId.split("::").pop()}
                </code>
                <span className="ml-auto tabular-nums text-[var(--color-text-tertiary)]">
                  {count} {count === 1 ? "fix" : "fixes"}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-[10px] leading-tight text-[var(--color-text-tertiary)]">
            Approximate: fixes are matched to symbols by line range, and lines move.
          </p>
        </div>
      ) : null}
    </CollapsibleSection>
  );
}

export function DependenciesSection({ signals }: { signals: FileSignals | null | undefined }) {
  if (!signals || !hasAnySignal(signals, ["topology"])) return null;
  return (
    <CollapsibleSection title="Dependencies" hint={dependencyHint(signals)}>
      <FileSignalsPanel signals={signals} groups={["topology"]} hideHeading />
    </CollapsibleSection>
  );
}

export function ElsewhereSection({
  related,
  relatedWorkHref,
  relatedError,
  onRelatedRetry,
  onNavigate,
  exclude,
  tally,
}: RelatedWorkSlotProps & {
  exclude: readonly RelatedLensName[];
  tally: { items: number; lenses: number };
}) {
  const failed = relatedError != null && tally.items === 0;
  if (!failed && tally.items === 0) return null;
  return (
    <CollapsibleSection
      title="Elsewhere for this file"
      hint={
        failed
          ? "couldn't load"
          : `in ${tally.lenses} other ${tally.lenses === 1 ? "view" : "views"}`
      }
    >
      {failed ? (
        <ApiError
          size="compact"
          title="Couldn't load the other views"
          message={toFriendlyMessage(relatedError)}
          {...(onRelatedRetry ? { onRetry: onRelatedRetry } : {})}
        />
      ) : (
        <RelatedWork
          related={related}
          relatedWorkHref={relatedWorkHref}
          onNavigate={onNavigate}
          exclude={exclude}
          hideHeading
        />
      )}
    </CollapsibleSection>
  );
}

/** The arithmetic behind the number at the top, categories only: the findings
 *  under each are already listed above. */
export function WhyScoreSection({ breakdown }: { breakdown: HealthFileDrawerProps["breakdown"] }) {
  if (!breakdown) return null;
  return (
    <CollapsibleSection title="Why this score" hint={breakdownHint(breakdown.categories)}>
      <ScoreBreakdown
        score={breakdown.score}
        totalDeduction={breakdown.total_deduction}
        categories={breakdown.categories}
        showFindings={false}
      />
    </CollapsibleSection>
  );
}

/**
 * What the history section holds, in words. Its counts are the body's
 * headline, so the hint names the kinds of evidence rather than restating them.
 */
function historyHint(s: FileSignals): string | undefined {
  const parts: string[] = [];
  if (s.prior_defect_count != null || s.commit_count_90d != null || s.age_days != null) {
    parts.push("change history");
  }
  if (s.primary_owner_name != null || s.recent_owner_name != null) parts.push("owners");
  if (s.fix_symbol_counts && Object.keys(s.fix_symbol_counts).length > 0) {
    parts.push("where fixes landed");
  }
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

/** One plain fact about the file's place in the graph; the body has the counts. */
function dependencyHint(s: FileSignals): string {
  if (s.in_degree == null) return "what it depends on";
  if (s.in_degree === 0) return "nothing depends on it";
  return s.out_degree === 0 ? "others depend on it, it depends on nothing" : "others depend on it";
}

/** The total is the body's arithmetic line, so the hint names the categories. */
function breakdownHint(categories: ScoreBreakdownCategory[]): string {
  const n = categories.length;
  const held = categories.filter((c) => c.capped).length;
  const base = `${n} ${n === 1 ? "category" : "categories"}`;
  return held > 0 ? `${base}, ${held} at ${held === 1 ? "its" : "their"} limit` : base;
}

/** A structural counter that may genuinely be unmeasured: say so instead of
 *  rendering a misleading 0. */
function MeasuredNum({ v }: { v: number | null }) {
  if (v == null) {
    return (
      <span
        className="text-xs text-[var(--color-text-tertiary)]"
        title="Not measured: no metric row is available for this file on this snapshot."
      >
        not measured
      </span>
    );
  }
  return <span className="text-[15px] font-semibold tabular-nums">{v}</span>;
}

/**
 * The two co-pillars and the structural counters, as one hairline `<dl>`, in
 * the words a developer uses, with the term of art one hover away. Scores
 * carry their band colour; counters are plain, because a nesting depth of 3 is
 * not good or bad news on its own.
 */
function MetricGrid({ metric }: { metric: HealthDrawerMetric }) {
  const cells: { label: string; tip?: string; value: React.ReactNode }[] = [
    {
      label: "Maintainability",
      value: <PillarScore v={metric.maintainability_score ?? null} />,
    },
    { label: "Performance", value: <PillarScore v={metric.performance_score ?? null} /> },
    {
      // "Line coverage", not "Tests": the paired-file flag below is the tests
      // cell, and two cells named alike in one grid is the vocabulary failure.
      label: "Line coverage",
      tip: "Share of the file's lines a coverage report saw run.",
      value: (
        <PlainValue>
          {metric.line_coverage_pct == null
            ? "not measured"
            : `${metric.line_coverage_pct.toFixed(0)}%`}
        </PlainValue>
      ),
    },
    {
      label: "Complexity",
      tip: "Cyclomatic complexity (CCN) of the most complex function: the number of independent paths through it.",
      value: <MeasuredNum v={metric.max_ccn} />,
    },
    {
      label: "Nesting depth",
      tip: "The deepest level of nested blocks in any function.",
      value: <MeasuredNum v={metric.max_nesting} />,
    },
    {
      label: "Lines of code",
      tip: "Non-blank, non-comment lines (NLOC).",
      value: <MeasuredNum v={metric.nloc} />,
    },
    {
      label: "Duplication",
      tip: "Share of lines that repeat a block found elsewhere.",
      value: (
        <PlainValue>
          {metric.duplication_pct == null
            ? "not measured"
            : `${metric.duplication_pct.toFixed(0)}%`}
        </PlainValue>
      ),
    },
    {
      label: "Test file",
      value: <PlainValue>{metric.has_test_file ? "Paired" : "None"}</PlainValue>,
    },
    {
      label: "Module",
      value: (
        <span
          className="block truncate font-mono text-xs text-[var(--color-text-primary)]"
          title={metric.module ?? undefined}
        >
          {metric.module ?? "none"}
        </span>
      ),
    },
  ];

  return (
    <dl className="grid grid-cols-2 border-y border-[var(--color-border-default)] sm:grid-cols-3">
      {cells.map((c, i) => (
        <div
          key={c.label}
          className={[
            "min-w-0 px-3 py-2.5",
            "border-[var(--color-border-default)]",
            // Hairlines between cells only; the outer edges come from border-y
            // on the wrapper, so cells never double up on a boundary.
            i % 2 === 1 ? "border-l" : "",
            i >= 2 ? "border-t" : "",
            "sm:border-l sm:border-t-0",
            i % 3 === 0 ? "sm:border-l-0" : "",
            i >= 3 ? "sm:border-t" : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          <dt className={`inline-flex items-center gap-1 ${MICRO_LABEL}`}>
            <span>{c.label}</span>
            {c.tip ? <InfoTip content={c.tip} label={`About ${c.label.toLowerCase()}`} /> : null}
          </dt>
          <dd className="mt-1">{c.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A 0-10 pillar score, band-coloured, or an honest "not measured". */
function PillarScore({ v }: { v: number | null }) {
  if (v == null) {
    return <span className="text-xs text-[var(--color-text-tertiary)]">not measured</span>;
  }
  return (
    <span
      className="text-[18px] font-semibold tabular-nums"
      style={{ color: healthBandColor(bandForScore(v)) }}
    >
      {formatScore(v)}
      <span className="text-xs font-normal text-[var(--color-text-tertiary)]">/10</span>
    </span>
  );
}

function PlainValue({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[15px] tabular-nums text-[var(--color-text-primary)]">{children}</span>
  );
}
