/**
 * The Dead Code tab's opening read.
 *
 * It replaces four `MetricCard`s in a grid, two of which held a stacked
 * label-and-count list inside the value slot. That is a table wearing a card:
 * seven figures at near-identical weight behind four uppercase labels, with
 * nothing leading and nothing saying what any of it means. A count of findings
 * is also the wrong figure to lead with, because the question this tab answers
 * is "what can I delete", and the answer is measured in lines.
 *
 * The kind breakdown moved into prose. Four kinds as four tiles read as four
 * equal problems; as a sentence they read as what they are, one pile sorted by
 * how it was found.
 */

import type { ReactNode } from "react";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";

import { PageLede } from "../shared/page-lede";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { formatNumber } from "../lib/format";

export interface DeadCodeLedeProps {
  summary: DeadCodeSummary;
  /**
   * Findings in the slice on screen. Passed so the lede can say when the
   * server-wide totals and the list below describe different populations,
   * rather than letting a capped page read as the whole repository.
   */
  shownCount?: number;
  truncated?: boolean;
  /** Rendered under the prose. The tab's one action. */
  action?: ReactNode;
}

/** How a finding was reached, in the words a person would use. */
const KIND_LABELS: Record<string, string> = {
  unreachable_file: "unreachable file",
  unused_export: "unused export",
  unused_internal: "unused internal symbol",
  zombie_package: "zombie package",
};

function kindPhrase(kind: string, count: number): string {
  const label = KIND_LABELS[kind] ?? kind.replace(/_/g, " ");
  return `${formatNumber(count)} ${label}${count === 1 ? "" : "s"}`;
}

/** "a, b and c" — an Oxford-comma-free list, built rather than interpolated so
 *  a repo with one kind does not read "1 unused export and". */
function joinPhrases(parts: string[]): string {
  if (parts.length <= 1) return parts[0] ?? "";
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

export function DeadCodeLede({
  summary,
  shownCount,
  truncated = false,
  action,
}: DeadCodeLedeProps) {
  const kinds = Object.entries(summary.by_kind)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
  const confidence = summary.confidence_summary;
  const high = confidence.high ?? 0;
  const medium = confidence.medium ?? 0;
  const low = confidence.low ?? 0;
  const graded = high + medium + low;
  const flagged = summary.total_lines;
  const share =
    flagged > 0 ? Math.round((summary.deletable_lines / flagged) * 100) : null;

  const stats: RibbonStat[] = [
    {
      label: "Findings",
      value: formatNumber(summary.total_findings),
      ...(truncated && shownCount != null
        ? { sub: `${formatNumber(shownCount)} on this page` }
        : {}),
    },
    {
      label: "Lines flagged",
      value: formatNumber(flagged),
      sub: "across every finding",
      hint: "Every line inside a dead-code finding, whether or not we would delete it unreviewed.",
    },
    {
      label: "High confidence",
      value: formatNumber(high),
      valueColor: high > 0 ? "text-[var(--color-success)]" : undefined,
      sub: "confidence 0.7 or better",
      hint: "No dynamic reference, no re-export, no entry point that we can see.",
    },
    {
      label: "Needs a look",
      value: formatNumber(medium + low),
      sub: "verify before deleting",
      hint: "Reachable only through something static analysis cannot follow: a dynamic import, a plugin registry, a framework hook.",
    },
    {
      label: "Deletion-ready",
      value: share == null ? "" : `${share}%`,
      sub: "of the flagged lines",
      hint: "High confidence and outside the config, bootstrap and environment paths we never auto-delete from.",
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <PageLede
        label="Reclaimable"
        value={formatNumber(summary.deletable_lines)}
        unit="lines"
        layout="beside"
        {...(action ? { action } : {})}
        figureFooter={
          graded > 0 ? (
            <ConfidenceSplit high={high} medium={medium} low={low} />
          ) : undefined
        }
      >
        {summary.total_findings === 0 ? (
          <p>
            Nothing in this repository is currently flagged as unreachable, unused or
            zombie. That is a real result rather than an absence: the analysis walked
            the import graph, the export surface and the package manifests and found
            no symbol it could reach from no entry point. Re-run it after a large
            refactor, when whole call paths tend to go quiet at once.
          </p>
        ) : (
          <>
            <p>
              <strong className="font-semibold text-[var(--color-text-primary)]">
                {formatNumber(summary.total_findings)} findings
              </strong>{" "}
              covering{" "}
              <strong className="font-semibold text-[var(--color-text-primary)]">
                {formatNumber(flagged)} lines
              </strong>{" "}
              have no reachable caller. They are{" "}
              {joinPhrases(kinds.map(([kind, count]) => kindPhrase(kind, count)))},
              found by walking the import graph and the export surface rather than by
              matching names.
            </p>

            <p className="mt-2.5">
              Of those,{" "}
              <strong className="font-semibold text-[var(--color-text-primary)]">
                {formatNumber(summary.deletable_lines)} lines
              </strong>{" "}
              are deletion-ready: high confidence, and outside the config, bootstrap
              and environment paths we never delete from unreviewed
              {share != null ? `, which is ${share}% of what is flagged` : ""}. That
              is the number the cleanup brief is seeded from.
            </p>

            <p className="mt-2.5">
              Confidence is not a hedge on the finding, it is a statement about the
              language. A Python entry point registered by string name and a
              TypeScript symbol re-exported through a barrel file are both invisible
              to a call-graph walk, and calling either one dead would be wrong.
            </p>
          </>
        )}
      </PageLede>

      <StatRibbon stats={stats} />
    </div>
  );
}

/**
 * The one second read belonging to the number itself: how much of the pile you
 * can act on now. Stepped down from the accent rather than reaching for three
 * new hues, because confidence is an ordered scale, not three categories.
 */
function ConfidenceSplit({
  high,
  medium,
  low,
}: {
  high: number;
  medium: number;
  low: number;
}) {
  const total = high + medium + low;
  const segments = [
    { key: "high", label: "high", count: high, bar: "bg-[var(--color-accent-primary)]" },
    {
      key: "medium",
      label: "medium",
      count: medium,
      bar: "bg-[color-mix(in_srgb,var(--color-accent-primary)_45%,var(--color-bg-inset))]",
    },
    {
      key: "low",
      label: "low",
      count: low,
      bar: "bg-[color-mix(in_srgb,var(--color-accent-primary)_18%,var(--color-bg-inset))]",
    },
  ].filter((s) => s.count > 0);

  return (
    <div className="space-y-1.5">
      <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
        {segments.map((s) => (
          <div
            key={s.key}
            className={s.bar}
            style={{ width: `${(s.count / total) * 100}%` }}
            aria-label={`${s.count} ${s.label} confidence`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-[var(--color-text-tertiary)]">
        {segments.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1 tabular-nums">
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${s.bar}`} />
            {formatNumber(s.count)} {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
