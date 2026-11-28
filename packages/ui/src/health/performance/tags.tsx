"use client";

import type { PerformanceEvidenceMark, PerformanceOpportunity } from "@repowise-dev/types/health";

import { LowerPriorityTag } from "../lower-priority-tag";
import { PreviewBadge } from "../preview-badge";

/** Each runtime evidence state as a word, and the reason beside it. Never a colour. */
function markWords(mark: PerformanceEvidenceMark): { word: string; why: string; ink: boolean } {
  switch (mark.state) {
    case "confirmed":
      return {
        word: "Confirmed",
        why:
          mark.basis === "query_count"
            ? "by query counts that grow"
            : mark.basis === "measured_in_tests"
              ? "by a measured test run"
              : "by a measured run",
        ink: true,
      };
    case "measured":
      if (mark.basis === "measured_in_tests") {
        return {
          word: "Measured in tests",
          why: mark.workload ? `in ${mark.workload}` : "",
          ink: true,
        };
      }
      return { word: "Measured", why: mark.workload ? `in ${mark.workload}` : "", ink: true };
    case "counter_evidence":
      return { word: "Counter-evidence", why: "query counts stay flat as the input grows", ink: false };
    case "ran":
      return { word: "Ran", why: "query counts read at one input size", ink: false };
    case "not_exercised":
      return {
        word: "Not exercised",
        why: mark.workloads?.length ? `in ${mark.workloads.join(", ")}` : "",
        ink: false,
      };
  }
}

/** The row's mark, or one read from the detail view's facets on an older server. */
function evidenceMarkOf(opportunity: PerformanceOpportunity): PerformanceEvidenceMark | null {
  if (opportunity.runtime_evidence) return opportunity.runtime_evidence;
  const facets = opportunity.facets;
  if (facets?.confirmed) return { state: "confirmed", basis: facets.confirmed };
  if (facets?.measured) return { state: "measured", workload: facets.measured };
  if (facets?.measured_in_tests) {
    return { state: "measured", basis: "measured_in_tests", workload: facets.measured_in_tests };
  }
  return null;
}

/** What runtime evidence says about a cause, when the server read any. */
function EvidenceMark({ opportunity }: { opportunity: PerformanceOpportunity }) {
  const mark = evidenceMarkOf(opportunity);
  if (!mark) return null;
  const { word, why, ink } = markWords(mark);
  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11px] text-[var(--color-text-tertiary)]">
      <EvidenceWordTag ink={ink}>{word}</EvidenceWordTag>
      {why ? <span>{why}</span> : null}
    </p>
  );
}

/** The tags a cause can carry, on its row and in its drawer: what runtime
 *  evidence says about it, why it can wait, and a language cell served as a
 *  preview. */
export function OpportunityTags({ opportunity }: { opportunity: PerformanceOpportunity }) {
  return (
    <>
      <EvidenceMark opportunity={opportunity} />
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
