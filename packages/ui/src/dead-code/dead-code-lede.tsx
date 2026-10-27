/**
 * The Dead Code tab's opening read: one figure (deletion-ready lines) and one
 * sentence saying how much more is flagged and when it was measured.
 *
 * The figure is the deletion-ready share, never the flagged total: flagged
 * includes low-confidence findings with known false-positive classes, so it
 * overstates what can go. Flagged stays as the quieter second sentence.
 * The confidence methodology lives on the label's info tip.
 */

import type { ReactNode } from "react";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";

import { PageLede } from "../shared/page-lede";
import { formatDateTime, formatNumber, formatRelativeTimeOrNull } from "../lib/format";

/** Counts a flagged sentence quotes. */
export interface DeadCodeFlaggedCounts {
  findings: number;
  lines: number;
}

/** Every string the lede renders, so a host can translate it. */
export interface DeadCodeLedeLabels {
  label: string;
  labelHint: string;
  unit: string;
  /** The unit when the figure is a finding count (deletion-ready findings of unknown size). */
  findingsUnit: (count: number) => string;
  /** Between the two sentences: a space in English, nothing in Chinese. */
  separator: string;
  /** No open findings. */
  clean: string;
  /** "12 findings are deletion-ready." */
  ready: (count: number) => string;
  /** Deletion-ready lines from a host that does not report the finding count. */
  readyUncounted: string;
  /** "130 more are flagged for review." */
  moreFlagged: (count: number) => string;
  /** The flagged total, after `readyUncounted`. */
  flaggedInAll: (c: DeadCodeFlaggedCounts) => string;
  /** Nothing deletion-ready, said as a plain fact. */
  noneReady: string;
  /** The flagged total, after `noneReady`. */
  flaggedForReview: (c: DeadCodeFlaggedCounts) => string;
}

function plural(n: number, word: string): string {
  return `${formatNumber(n)} ${word}${n === 1 ? "" : "s"}`;
}

const flagged = (c: DeadCodeFlaggedCounts) =>
  `${plural(c.findings, "finding")} across ${plural(c.lines, "line")}`;
const isAre = (n: number) => (n === 1 ? "is" : "are");

export const DEFAULT_DEAD_CODE_LEDE_LABELS: DeadCodeLedeLabels = {
  label: "Deletion-ready",
  labelHint:
    "Deletion-ready means high confidence and outside the config, bootstrap and environment paths we never delete from unreviewed. Confidence is a statement about the language, not a hedge: a Python entry point registered by name or a TypeScript symbol re-exported through a barrel file is invisible to a call-graph walk.",
  unit: "lines",
  findingsUnit: (count) => (count === 1 ? "finding" : "findings"),
  separator: " ",
  clean: "No open findings remain.",
  ready: (count) => `${plural(count, "finding")} ${isAre(count)} deletion-ready.`,
  readyUncounted: "These lines are deletion-ready.",
  moreFlagged: (count) => `${formatNumber(count)} more ${isAre(count)} flagged for review.`,
  flaggedInAll: (c) => `${flagged(c)} ${isAre(c.findings)} flagged in all.`,
  noneReady: "Nothing is deletion-ready right now.",
  flaggedForReview: (c) => `${flagged(c)} ${isAre(c.findings)} flagged for review.`,
};

export interface DeadCodeLedeProps {
  summary: DeadCodeSummary;
  /** Rendered under the prose. The tab's one action. */
  action?: ReactNode;
  /** Replaces the posture sentence, e.g. with the all-clear state. */
  children?: ReactNode;
  labels?: Partial<DeadCodeLedeLabels>;
}

/** "Analysed 2d ago." with the exact time on hover, or nothing when unknown. */
export function AnalysedAt({ at }: { at: string | null | undefined }) {
  const relative = formatRelativeTimeOrNull(at, "");
  if (!at || !relative) return null;
  return (
    <>
      {" "}
      <time dateTime={at} title={formatDateTime(at)}>
        Analysed {relative}.
      </time>
    </>
  );
}

/** The posture: the deletion-ready read, then the flagged total (or null). */
function posture(summary: DeadCodeSummary, l: DeadCodeLedeLabels): [string, string | null] {
  const findings = summary.total_findings;
  const total = { findings, lines: summary.total_lines };
  if (findings === 0) return [l.clean, null];
  const ready = summary.safe_to_delete_count;
  // The count when the host sends it, since a finding of unknown size adds
  // no lines; the lines otherwise.
  if (ready === 0 || (ready == null && summary.deletable_lines <= 0)) {
    return [l.noneReady, l.flaggedForReview(total)];
  }
  if (ready == null) return [l.readyUncounted, l.flaggedInAll(total)];
  const rest = findings - ready;
  return [l.ready(ready), rest > 0 ? l.moreFlagged(rest) : null];
}

export function DeadCodeLede({ summary, action, children, labels }: DeadCodeLedeProps) {
  const l = { ...DEFAULT_DEAD_CODE_LEDE_LABELS, ...labels };
  const [lead, rest] = posture(summary, l);
  const ready = summary.safe_to_delete_count ?? 0;
  // Deletion-ready findings whose size is unknown add no lines: count them
  // instead of showing "0 lines" beside "2 findings are deletion-ready".
  const byCount = ready > 0 && summary.deletable_lines <= 0;

  return (
    <PageLede
      label={l.label}
      labelHint={l.labelHint}
      value={formatNumber(byCount ? ready : Math.max(summary.deletable_lines, 0))}
      unit={byCount ? l.findingsUnit(ready) : l.unit}
      layout="beside"
      {...(action ? { action } : {})}
    >
      {children ?? (
        <p>
          <span className="font-semibold text-[var(--color-text-primary)]">{lead}</span>
          {rest && (
            <>
              {l.separator}
              <span className="text-[var(--color-text-tertiary)]">{rest}</span>
            </>
          )}
          <AnalysedAt at={summary.analyzed_at} />
        </p>
      )}
    </PageLede>
  );
}
