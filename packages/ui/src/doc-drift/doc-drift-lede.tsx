/**
 * The Doc drift tab's opening read.
 *
 * The figure is the count of findings, because unlike dead code there is no
 * second unit here: a drift finding is one sentence in one document that is no
 * longer true, and lines would say nothing. The document count sits beside it
 * because it is what turns the figure into work — twelve findings across two
 * documents is an afternoon, twelve across twelve is a sweep.
 *
 * The basis sentence is not decoration and is not optional. Most references in
 * a real tree are uncheckable by design, so a bare count implies a coverage
 * this detector does not have. It is rendered as prose next to the figure
 * rather than as a footnote for exactly that reason.
 */

import {
  DOC_DRIFT_CONFIDENCE,
  docDriftKindLabel,
  type DocDriftSummary,
} from "@repowise-dev/types/doc-drift";

import { PageLede } from "../shared/page-lede";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { formatNumber } from "../lib/format";

export interface DocDriftLedeProps {
  summary: DocDriftSummary;
  /** Rows on screen, when fewer than the summary counts. */
  shownCount?: number;
  truncated?: boolean;
}

/** "a, b and c", built rather than interpolated so one kind does not trail "and". */
function joinPhrases(parts: string[]): string {
  if (parts.length <= 1) return parts[0] ?? "";
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

export function DocDriftLede({
  summary,
  shownCount,
  truncated = false,
}: DocDriftLedeProps) {
  const high = summary.confidence.high ?? 0;
  const medium = summary.confidence.medium ?? 0;
  const low = summary.confidence.low ?? 0;
  const graded = high + medium + low;

  const kinds = Object.entries(summary.by_kind)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);

  const stats: RibbonStat[] = [
    {
      label: "Findings",
      value: formatNumber(summary.findings_total),
      ...(truncated && shownCount != null
        ? { sub: `${formatNumber(shownCount)} on this page` }
        : {}),
      hint: "One assertion a document makes that the repository no longer satisfies.",
    },
    {
      label: "Documents",
      value: formatNumber(summary.documents),
      sub: "carry at least one",
      hint: "The files you would edit. A finding is filed against the document, never against the file it names.",
    },
    {
      label: "Near-certain",
      value: formatNumber(high),
      valueColor: high > 0 ? "text-[var(--color-warning)]" : undefined,
      sub: `confidence ${DOC_DRIFT_CONFIDENCE.HIGH} or better`,
      hint: "A path with no candidate anywhere in the tree, or a heading link whose heading is gone.",
    },
    {
      label: "Worth a look",
      value: formatNumber(medium + low),
      sub: "check before editing",
      hint: "Most often a path inside a guide that teaches rather than describes, where the author may have invented the example.",
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <PageLede
        label="Drifted"
        value={formatNumber(summary.findings_total)}
        unit={summary.findings_total === 1 ? "assertion" : "assertions"}
        layout="beside"
        figureFooter={
          graded > 0 ? (
            <ConfidenceSplit high={high} medium={medium} low={low} />
          ) : undefined
        }
      >
        {summary.findings_total === 0 ? (
          <>
            <p>
              Every reference this detector could resolve still resolves. No document
              names a file, a heading or a command that the repository has since moved
              or removed.
            </p>
            <p className="mt-2.5">{summary.findings_basis}</p>
          </>
        ) : (
          <>
            <p>
              <strong className="font-semibold text-[var(--color-text-primary)]">
                {formatNumber(summary.findings_total)}{" "}
                {summary.findings_total === 1 ? "assertion" : "assertions"}
              </strong>{" "}
              across{" "}
              <strong className="font-semibold text-[var(--color-text-primary)]">
                {formatNumber(summary.documents)}{" "}
                {summary.documents === 1 ? "document" : "documents"}
              </strong>{" "}
              no longer hold
              {kinds.length > 0 ? (
                <>
                  {" "}
                  &mdash; they are{" "}
                  {joinPhrases(
                    kinds.map(
                      ([kind, count]) =>
                        `${formatNumber(count)} ${docDriftKindLabel(kind).toLowerCase()}${count === 1 ? "" : "s"}`,
                    ),
                  )}
                </>
              ) : null}
              . Each one names the document to edit, not the file it points at.
            </p>
            <p className="mt-2.5">{summary.findings_basis}</p>
            <p className="mt-2.5">
              Confidence here is a statement about the evidence, not a hedge. A path
              with no candidate anywhere in the tree is near-certain; the same
              evidence inside a contributor guide is not, because a guide teaching
              you to add a file may name one that was never meant to exist.
            </p>
          </>
        )}
      </PageLede>

      <StatRibbon stats={stats} />
    </div>
  );
}

/**
 * How the pile splits by confidence. Stepped down from one family rather than
 * reaching for three hues, because confidence is an ordered scale and not
 * three categories; amber leads because drift is an attention state.
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
    { key: "high", label: "near-certain", count: high, bar: "bg-[var(--color-warning)]" },
    {
      key: "medium",
      label: "medium",
      count: medium,
      bar: "bg-[color-mix(in_srgb,var(--color-warning)_45%,var(--color-bg-inset))]",
    },
    {
      key: "low",
      label: "low",
      count: low,
      bar: "bg-[color-mix(in_srgb,var(--color-warning)_18%,var(--color-bg-inset))]",
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
