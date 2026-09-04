"use client";

import * as React from "react";
import {
  DECISION_CURRENCY_DESCRIPTIONS,
  DECISION_CURRENCY_LABELS,
  DECISION_LANES,
  DECISION_SOURCES,
  decisionAcceptanceBlockers,
  decisionSourceLabel,
  isRetiredDecisionSource,
} from "@repowise-dev/types/decisions";
import type {
  DecisionCurrency,
  DecisionLane,
  DecisionRecord,
} from "@repowise-dev/types/decisions";
import { ApiError } from "../shared/api-error";
import { EmptyState } from "../shared/empty-state";
import { ViewTabs, type ViewTab } from "../shared/view-tabs";
import { Button } from "../ui/button";
import { VerificationBadge } from "./verification-badge";
import { describeRecordStaleness } from "./decision-staleness";
import { stripMarkdown } from "../lib/format";

/**
 * The review lanes: what governs, what is waiting to be reviewed, and what has
 * been retired.
 *
 * The decisions page had one table sorted by a status column, which is the
 * wrong axis now that authority is an acceptance rather than a status. On a
 * live index that table opens on 505 rows of which 381 are candidates nobody
 * has looked at, mixed in with the 122 that govern and separated only by a
 * word in a column. A reader could not tell a rule from a guess without
 * reading every row, and an agent quoting the page could not either.
 *
 * The lanes are that separation, and they are semantic before they are
 * visual: a candidate is a request to review something, never an instruction.
 * It gets its own lane, its own verb, and the evidence it was drawn from, so
 * the decision to accept it can be made from the row.
 *
 * Presentation and lane state only. The host fetches, pages and writes, per
 * the convention the rest of this directory follows, so one implementation
 * serves the OSS app and hosted.
 */

/**
 * The lane vocabulary is shared and pinned against the engine's, so this file
 * declares neither the lanes nor their words. A second list here would be the
 * second registry this whole change exists to remove.
 */
export type { DecisionLane };
export { DECISION_LANES };

const LANE_LABEL: Record<DecisionLane, string> = {
  active: DECISION_CURRENCY_LABELS.active,
  candidates: "Candidates",
  needs_review: DECISION_CURRENCY_LABELS.needs_review,
  uncheckable: DECISION_CURRENCY_LABELS.uncheckable,
  history: "History",
};

/**
 * What the lane holds, and what the reader is being asked to do with it. Shown
 * under the tab row, because a lane whose name is a noun still owes the reader
 * a sentence about why its rows are in it.
 */
const LANE_DESCRIPTION: Record<DecisionLane, string> = {
  active:
    "Accepted decisions that still describe the code they name. These are the rules, and they are what an agent is given when it edits a governed file.",
  candidates:
    "Records the indexer inferred and nobody has accepted. They govern nothing and reach no agent. Read the evidence, then accept the ones that are real.",
  needs_review:
    "Accepted decisions whose files have moved since. They still bind, and they are worth re-reading before you rely on one.",
  uncheckable:
    "Accepted decisions that name no file or module. Nothing can check them against the code and no agent editing a file will be given one. Add the paths they govern and they move to Active.",
  history:
    "Decisions that were accepted and then withdrawn, superseded or dismissed. Kept so the record of what changed survives.",
};

/** An empty lane says what will fill it and what the reader can do next. */
const LANE_EMPTY: Record<DecisionLane, string> = {
  active:
    "Nothing here governs yet. Accept a candidate, or record a decision yourself, and it will appear here.",
  candidates:
    "No candidates are waiting. The indexer raises them from pull requests, commits, comments and your own sessions.",
  needs_review: "No accepted decision has drifted from the code it names.",
  uncheckable: "Every accepted decision names the code it governs.",
  history: "Nothing has been retired yet.",
};

export interface DecisionReviewLanesProps {
  lane: DecisionLane;
  onLaneChange: (lane: DecisionLane) => void;
  /**
   * Rows for the current lane. The caller filters, because the lane is a
   * server query parameter and this shows one page of it.
   */
  decisions: DecisionRecord[] | undefined;
  /**
   * Per-lane totals for the tab badges. A lane whose count is unknown renders
   * without one rather than with a number nobody measured.
   */
  counts?: Partial<Record<DecisionLane, number>>;
  /**
   * Narrow the lane to one capture source, or `"all"`. A separate axis from
   * the lane: the lane asks who accepted a record, this asks where it came
   * from, and the two never select the same thing.
   */
  source?: string;
  onSourceChange?: (source: string) => void;
  /** Accepts the candidate. Omit where the surface cannot write. */
  onAccept?: (decision: DecisionRecord) => void;
  /** Dismisses the candidate as a tombstone. Omit where the surface cannot write. */
  onDismiss?: (decision: DecisionRecord) => void;
  /**
   * Why writes are unavailable, shown in place of the verbs. A control that
   * cannot act has to expose its reason, and a review surface that silently
   * renders no actions reads as one with nothing to do.
   */
  readOnlyReason?: string;
  /**
   * Ids whose review write is in flight; their verbs are disabled. A set
   * rather than one id, because two reviews can overlap and a single value
   * re-enabled the second row the moment the first resolved.
   */
  pendingIds?: ReadonlySet<string>;
  repoId: string;
  linkPrefix?: string;
  LinkComponent?: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  error?: unknown;
  isLoading?: boolean;
  onRetry?: () => void;
}

export function DecisionReviewLanes({
  lane,
  onLaneChange,
  decisions,
  counts,
  source = "all",
  onSourceChange,
  onAccept,
  onDismiss,
  readOnlyReason,
  pendingIds,
  repoId,
  linkPrefix,
  LinkComponent = "a",
  error,
  isLoading,
  onRetry,
}: DecisionReviewLanesProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const Link = LinkComponent;

  const tabs: ViewTab[] = DECISION_LANES.map((id) => {
    const badge = counts?.[id];
    return badge === undefined
      ? { id, label: LANE_LABEL[id] }
      : { id, label: LANE_LABEL[id], badge };
  });

  const rows = decisions ?? [];

  return (
    <ViewTabs
      tabs={tabs}
      value={lane}
      onValueChange={(id) => onLaneChange(id as DecisionLane)}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="min-w-0 max-w-[68ch] flex-1 text-xs text-[var(--color-text-secondary)]">
          {LANE_DESCRIPTION[lane]}
        </p>
        {/* Built from the shared source list rather than from the loaded rows:
            a lane page is one window, so a source absent from it would become
            unselectable, which is the same defect wearing a dynamic coat. */}
        {onSourceChange && (
          <select
            value={source}
            onChange={(e) => onSourceChange(e.target.value)}
            aria-label="Filter by source"
            className="shrink-0 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-primary)]"
          >
            <option value="all">All sources</option>
            {DECISION_SOURCES.filter((s) => !isRetiredDecisionSource(s)).map(
              (s) => (
                <option key={s} value={s}>
                  {decisionSourceLabel(s)}
                </option>
              ),
            )}
          </select>
        )}
      </div>

      {error ? (
        <ApiError
          title="Couldn't load this lane"
          message="An error occurred while fetching decisions."
          {...(onRetry ? { onRetry } : {})}
        />
      ) : rows.length === 0 ? (
        isLoading ? null : (
          <EmptyState
            title={`Nothing in ${LANE_LABEL[lane].toLowerCase()}`}
            description={
              source === "all"
                ? LANE_EMPTY[lane]
                : `No ${decisionSourceLabel(source)} record is in this lane. The lane itself may not be empty; clear the source filter to see it.`
            }
          />
        )
      ) : (
        // Hairline-separated rows rather than a table or a grid of cards: the
        // rows are unequal in height because a candidate carries its evidence,
        // and repeating a bordered container at one weight per record turns
        // the lane into box soup.
        <ul className="divide-y divide-[var(--color-border-subtle)] border-t border-[var(--color-border-subtle)]">
          {rows.map((d) => (
            <DecisionLaneRow
              key={d.id}
              decision={d}
              lane={lane}
              href={`${prefix}/decisions/${d.id}`}
              Link={Link}
              {...(onAccept ? { onAccept } : {})}
              {...(onDismiss ? { onDismiss } : {})}
              {...(readOnlyReason ? { readOnlyReason } : {})}
              pending={pendingIds?.has(d.id) ?? false}
            />
          ))}
        </ul>
      )}
    </ViewTabs>
  );
}

interface RowProps {
  decision: DecisionRecord;
  lane: DecisionLane;
  href: string;
  Link: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  onAccept?: (decision: DecisionRecord) => void;
  onDismiss?: (decision: DecisionRecord) => void;
  readOnlyReason?: string;
  pending?: boolean;
}

const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

function DecisionLaneRow({
  decision: d,
  lane,
  href,
  Link,
  onAccept,
  onDismiss,
  readOnlyReason,
  pending,
}: RowProps) {
  const staleness = describeRecordStaleness(d);
  const currency = d.currency ?? null;
  const evidence = d.evidence_preview;
  const scope = [...d.affected_files, ...d.affected_modules];
  // The same four checks the engine refuses on, asked before the button is
  // drawn. A candidate that names nothing already renders "names nothing"
  // beside an Accept that could only fail; predicting the refusal is what
  // turns that into a control the reader can understand.
  const blockers = decisionAcceptanceBlockers(d);

  return (
    <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Link
            href={href}
            className="text-sm font-medium text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)]"
          >
            {stripMarkdown(d.title)}
          </Link>
          {/* Marked only where it is an exception. An accepted decision that
              still describes its code is the quiet default and carries no
              badge; a currency the reader cannot guess does. */}
          {currency !== null && currency !== "active" && (
            <CurrencyMark currency={currency as DecisionCurrency} />
          )}
          {d.verification && d.verification !== "exact" && (
            <VerificationBadge verification={d.verification} />
          )}
        </div>

        <p className="text-xs text-[var(--color-text-secondary)]">
          {stripMarkdown(d.decision || d.title)}
        </p>

        {/* Candidates carry the quote they were drawn from. It is the whole
            basis for accepting or dismissing one, and sending the reader to a
            detail page to find it is what leaves a review queue unworked. */}
        {lane === "candidates" && evidence?.source_quote && (
          <figure className="space-y-1 border-l-2 border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-3 py-2">
            <blockquote className="text-xs italic text-[var(--color-text-secondary)]">
              &ldquo;{stripMarkdown(evidence.source_quote)}&rdquo;
            </blockquote>
            <figcaption className={MICRO_LABEL}>
              {decisionSourceLabel(evidence.source)}
              {evidence.evidence_file ? ` · ${evidence.evidence_file}` : ""}
              {evidence.evidence_line != null
                ? `:${evidence.evidence_line}`
                : ""}
              {(d.evidence_count ?? 0) > 1
                ? ` · ${(d.evidence_count ?? 0) - 1} more`
                : ""}
            </figcaption>
          </figure>
        )}

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className={MICRO_LABEL}>{decisionSourceLabel(d.source)}</span>
          {/* One path reads as the path. Several read as a count, with the
              list on the title, because the alternative is a row whose
              primary label is crowded off by its own metadata. */}
          <span className={MICRO_LABEL} title={scope.join(", ")}>
            {scope.length === 0
              ? "names nothing"
              : scope.length === 1
                ? scope[0]
                : `${scope.length} paths`}
          </span>
          {staleness.kind === "moved" && (
            <span className={MICRO_LABEL} title={staleness.sentence}>
              {staleness.short} changed
            </span>
          )}
        </div>
      </div>

      {/* One primary verb per row, and only in the lane where it means
          something. Accepting is the review action; everything else on the
          record lives on its detail page. */}
      {lane === "candidates" && (
        <div className="flex shrink-0 flex-col items-start gap-1 sm:items-end">
          {readOnlyReason ? (
            <span className="text-xs text-[var(--color-text-tertiary)]">
              {readOnlyReason}
            </span>
          ) : (
            <>
              <div className="flex items-center gap-2">
                {onDismiss && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={pending}
                    onClick={() => onDismiss(d)}
                  >
                    Dismiss
                  </Button>
                )}
                {onAccept && (
                  <Button
                    size="sm"
                    disabled={pending || blockers.length > 0}
                    title={blockers.length ? blockers.join("; ") : undefined}
                    onClick={() => onAccept(d)}
                  >
                    {pending ? "Accepting…" : "Accept"}
                  </Button>
                )}
              </div>
              {/* Dismissing is always available: a candidate that cannot be
                  accepted is exactly the one worth tombstoning. The sentence
                  is text rather than only a tooltip, because a disabled
                  button takes no focus and a tooltip on it is mouse-only. */}
              {onAccept && blockers.length > 0 && (
                <p className="max-w-[28ch] text-right text-xs text-[var(--color-text-tertiary)]">
                  Cannot accept: {blockers.join("; ")}. Fill it in with{" "}
                  <code className="font-mono text-[11px]">
                    repowise decision confirm {d.id.slice(0, 8)}
                  </code>
                  .
                </p>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * A currency the reader cannot infer from the row, as a dot plus the word.
 *
 * Deliberately not semantic colour. `needs_review` is not a warning and
 * `uncheckable` is not an error: both describe decisions somebody accepted,
 * and painting them amber would put them on the health scale, where amber
 * means the code is in trouble. The word carries the meaning, the title
 * carries the sentence.
 */
function CurrencyMark({ currency }: { currency: DecisionCurrency }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] text-[var(--color-text-tertiary)]"
      title={DECISION_CURRENCY_DESCRIPTIONS[currency]}
    >
      <span
        aria-hidden
        className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-text-tertiary)]"
      />
      {DECISION_CURRENCY_LABELS[currency]}
    </span>
  );
}
