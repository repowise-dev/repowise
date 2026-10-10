/**
 * The Dead Code tab's opening read: one figure (reclaimable lines) and one
 * sentence saying what it is a share of and when it was measured.
 *
 * Earlier versions added three paragraphs, a confidence bar and a five-stat
 * ribbon under the figure; every one of them restated the same three numbers.
 * The confidence methodology lives on the label's info tip instead.
 */

import type { ReactNode } from "react";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";

import { PageLede } from "../shared/page-lede";
import { formatDateTime, formatNumber, formatRelativeTimeOrNull } from "../lib/format";

export interface DeadCodeLedeProps {
  summary: DeadCodeSummary;
  /** Rendered under the prose. The tab's one action. */
  action?: ReactNode;
  /** Replaces the posture sentence, e.g. with the all-clear state. */
  children?: ReactNode;
}

const CONFIDENCE_HINT =
  "Deletion-ready means high confidence and outside the config, bootstrap and environment paths we never delete from unreviewed. Confidence is a statement about the language, not a hedge: a Python entry point registered by name or a TypeScript symbol re-exported through a barrel file is invisible to a call-graph walk.";

function plural(n: number, word: string): string {
  return `${formatNumber(n)} ${word}${n === 1 ? "" : "s"}`;
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

export function DeadCodeLede({ summary, action, children }: DeadCodeLedeProps) {
  const findings = summary.total_findings;
  const strong = "font-semibold text-[var(--color-text-primary)]";

  return (
    <PageLede
      label="Reclaimable"
      labelHint={CONFIDENCE_HINT}
      value={formatNumber(summary.deletable_lines)}
      unit="lines"
      layout="beside"
      {...(action ? { action } : {})}
    >
      {children ?? (
        <p>
          {findings === 0 ? (
            "No open findings remain."
          ) : (
            <>
              <strong className={strong}>{plural(findings, "finding")}</strong> across{" "}
              <strong className={strong}>{plural(summary.total_lines, "line")}</strong>{" "}
              {findings === 1 ? "has" : "have"} no reachable caller. Only the deletion-ready
              share is counted above.
            </>
          )}
          <AnalysedAt at={summary.analyzed_at} />
        </p>
      )}
    </PageLede>
  );
}
