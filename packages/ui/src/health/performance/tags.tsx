"use client";

import type { PerformanceOpportunity } from "@repowise-dev/types/health";

import { LowerPriorityTag } from "../lower-priority-tag";
import { PreviewBadge } from "../preview-badge";

const CONFIRMED_BY: Record<string, string> = {
  measured: "by a measured run",
  query_count: "by query counts that grow",
};

/** What runtime evidence says about a cause, when the server read any: the
 *  workload that measured its function, and what confirmed it. */
function EvidenceMarks({ opportunity }: { opportunity: PerformanceOpportunity }) {
  const measured = opportunity.facets?.measured;
  const confirmedBy = opportunity.facets?.confirmed
    ? CONFIRMED_BY[opportunity.facets.confirmed]
    : undefined;
  if (!measured && !confirmedBy) return null;
  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11px] text-[var(--color-text-tertiary)]">
      {measured ? (
        <>
          <EvidenceWordTag ink>Measured</EvidenceWordTag>
          <span>
            in <code className="font-mono">{measured}</code>
          </span>
        </>
      ) : null}
      {confirmedBy ? (
        <>
          <EvidenceWordTag ink>Confirmed</EvidenceWordTag>
          <span>{confirmedBy}</span>
        </>
      ) : null}
    </p>
  );
}

/** The tags a cause can carry, on its row and in its drawer: what runtime
 *  evidence says about it, why it can wait, and a language cell served as a
 *  preview. */
export function OpportunityTags({ opportunity }: { opportunity: PerformanceOpportunity }) {
  return (
    <>
      <EvidenceMarks opportunity={opportunity} />
      {opportunity.lower_priority ? (
        <p className="mt-1">
          <LowerPriorityTag reason={opportunity.lower_priority} />
        </p>
      ) : null}
      {opportunity.preview ? (
        <p className="mt-1">
          <PreviewBadge note={opportunity.preview} />
        </p>
      ) : null}
    </>
  );
}

const WORD_TAG_CLS =
  "inline-block whitespace-nowrap rounded border px-1 font-mono text-[10px] uppercase leading-4 tracking-[0.12em]";

/**
 * A word in a bordered tag, on the `VerificationTag` pattern: what runtime
 * evidence says about a row. A word, never a colour; `ink` lifts the one
 * that carries news. With `onClick` it is a button, for a tag that opens what
 * it names.
 */
export function EvidenceWordTag({
  children,
  ink = false,
  onClick,
  label,
}: {
  children: React.ReactNode;
  ink?: boolean;
  onClick?: (() => void) | undefined;
  /** The button's accessible name, when the word alone does not say what it opens. */
  label?: string | undefined;
}) {
  const tone = ink
    ? "border-[var(--color-text-tertiary)] text-[var(--color-text-primary)]"
    : "border-[var(--color-border-default)] text-[var(--color-text-secondary)]";
  if (!onClick) return <span className={`${WORD_TAG_CLS} ${tone}`}>{children}</span>;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`${WORD_TAG_CLS} ${tone} hover:border-[var(--color-border-hover)] hover:text-[var(--color-accent-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]`}
    >
      {children}
    </button>
  );
}
