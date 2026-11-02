"use client";

import * as React from "react";
import {
  DECISION_CURRENCY_DESCRIPTIONS,
  DECISION_CURRENCY_LABELS,
  DECISION_KIND_LABELS,
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
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import { ViewTabs, type ViewTab } from "../shared/view-tabs";
import { Button } from "../ui/button";
import { cn } from "../lib/cn";
import { VerificationBadge } from "./verification-badge";
import { describeRecordStaleness } from "./decision-staleness";
import { DecisionReviewDrawer } from "./decision-review-drawer";
import type { BulkReviewResult } from "./bulk-review";
import { useBulkSelection, type BulkState, type Selection } from "./use-bulk-selection";
import {
  formatDate,
  formatRelativeTimeOrNull,
  parseDateOrNull,
  stripMarkdown,
} from "../lib/format";

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
    "Decisions that were accepted and then withdrawn, superseded or dismissed, or whose files no longer exist. Kept so the record of what changed survives.",
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
  /**
   * The order the host fetched the rows in. `"review"` is the server's review
   * queue order (`sort=review`), and the lane says so above the table. Left
   * out, the rows are shown in whatever order they came and nothing is
   * claimed about it.
   */
  order?: "review";
  /**
   * Reaffirms an accepted decision whose files moved, after re-reading it.
   * Offered in the Needs review lane only. Omit where the surface cannot write.
   */
  onReaffirm?: (decision: DecisionRecord) => void;
  /**
   * Dismisses a selection of candidates. Turns on row selection in the
   * Candidates lane. `onProgress` takes the count settled so far; the result
   * says which went through and which were refused, and the lane reports it.
   * `reviewEach` is the loop most hosts need.
   */
  onDismissMany?: (
    decisions: DecisionRecord[],
    onProgress: (settled: number) => void,
  ) => Promise<BulkReviewResult>;
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
  order,
  onReaffirm,
  onDismissMany,
}: DecisionReviewLanesProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const Link = LinkComponent as LinkLike;
  const [openId, setOpenId] = React.useState<string | null>(null);
  const rows = decisions ?? [];
  // Looked up, not held, so a refresh after a review shows the record
  // as it now is, and a record that left the lane closes the drawer.
  const open = rows.find((d) => d.id === openId) ?? null;
  const reviewable = lane === "candidates";
  const reaffirmable = lane === "needs_review" && onReaffirm !== undefined;
  const selectable =
    lane === "candidates" && onDismissMany !== undefined && !readOnlyReason;

  const bulkRun = useBulkSelection(rows, lane, selectable ? onDismissMany : undefined);
  const { selected, bulk } = bulkRun;

  // Acting from the drawer moves it to the next record, which is what makes
  // the drawer a queue and not a detour. Read before the host refreshes
  // the rows, because the record acted on is about to leave them.
  const actAndAdvance = (
    act: (decision: DecisionRecord) => void,
    d: DecisionRecord,
  ) => {
    const at = rows.findIndex((r) => r.id === d.id);
    const next = rows[at + 1] ?? null;
    act(d);
    setOpenId(next?.id ?? null);
  };

  const columns = buildColumns({
    lane,
    showHotspots: rows.some((d) => Array.isArray(d.hotspot_files)),
    onAccept,
    onDismiss,
    onReaffirm,
    readOnlyReason,
    pendingIds,
    selection: selectable ? bulkRun.selection : undefined,
  });

  const tabs: ViewTab[] = DECISION_LANES.map((id) => {
    const badge = counts?.[id];
    return badge === undefined
      ? { id, label: LANE_LABEL[id] }
      : { id, label: LANE_LABEL[id], badge };
  });

  return (
    <ViewTabs
      tabs={tabs}
      value={lane}
      onValueChange={(id) => onLaneChange(id as DecisionLane)}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-[68ch] flex-1 space-y-1.5 text-xs">
          <p className="text-[var(--color-text-secondary)]">{LANE_DESCRIPTION[lane]}</p>
          {order === "review" && (
            <p className="text-[var(--color-text-tertiary)]">{reviewOrderNote(lane)}</p>
          )}
        </div>
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
            tone={
              source !== "all"
                ? "filtered"
                : lane === "needs_review" || lane === "uncheckable"
                  ? "positive"
                  : "neutral"
            }
            title={`Nothing in ${LANE_LABEL[lane].toLowerCase()}`}
            description={
              source === "all"
                ? LANE_EMPTY[lane]
                : `No ${decisionSourceLabel(source)} record is in this lane. The lane itself may not be empty; clear the source filter to see it.`
            }
          />
        )
      ) : (
        <>
          {selectable && (selected.size > 0 || bulk !== null) && (
            <BulkBar
              selected={selected.size}
              bulk={bulk}
              onDismiss={() => void bulkRun.dismissSelected()}
              onClear={bulkRun.clear}
            />
          )}
          {/* A table, now that a row is one record at one height. The rows
              used to be unequal because a candidate carried its whole quote;
              the quote is clamped here and read in full in the drawer, so the
              queue scans as columns and a reviewer can compare what is
              waiting instead of reading it top to bottom. */}
          <ResponsiveTable
            columns={columns}
            rows={rows}
            rowKey={(d) => d.id}
            onRowClick={(d) => setOpenId(d.id)}
            selectedKey={openId}
            caption={`${LANE_LABEL[lane]} decisions`}
            bare
            className="border-y border-[var(--color-border-subtle)]"
          />
        </>
      )}
      <DecisionReviewDrawer
        decision={open}
        reviewable={reviewable}
        onReaffirm={
          reaffirmable && onReaffirm ? (d) => actAndAdvance(onReaffirm, d) : undefined
        }
        onClose={() => setOpenId(null)}
        onAccept={onAccept ? (d) => actAndAdvance(onAccept, d) : undefined}
        onDismiss={onDismiss ? (d) => actAndAdvance(onDismiss, d) : undefined}
        readOnlyReason={readOnlyReason}
        pending={open ? (pendingIds?.has(open.id) ?? false) : false}
        href={open ? `${prefix}/decisions/${open.id}` : prefix}
        Link={Link}
        marks={
          open ? (
            <>
              {open.currency && open.currency !== "active" && (
                <CurrencyMark currency={open.currency as DecisionCurrency} />
              )}
              <Provenance decision={open} />
            </>
          ) : null
        }
      />
    </ViewTabs>
  );
}

/**
 * The rule `review_queue_order` applies on the server, said once above the
 * table, not as a score per row: a number would invite reading the gap
 * between two rows as meaningful, and it is only an order.
 */
function reviewOrderNote(lane: DecisionLane): string {
  // Only candidates can be accepted, so only their note names that step.
  return lane === "candidates"
    ? "Most in need of a look first: records that can be accepted as they stand, then those naming churn hotspots, then those whose files have changed most since they were recorded."
    : "Most in need of a look first: records naming churn hotspots, then those whose files have changed most since they were recorded.";
}

/**
 * Where a quote came from, as row metadata: the 11px tertiary line the rest of
 * the row's meta uses, in sentence case. Not the uppercase micro label, which
 * reads as a heading and outshouted the quote it describes.
 */
const EVIDENCE_META = "text-2xs text-[var(--color-text-tertiary)]";


const NUMERIC_CELL = "font-mono text-xs tabular-nums whitespace-nowrap";

type LinkLike = React.ElementType<{
  href: string;
  className?: string;
  children: React.ReactNode;
}>;

interface ColumnContext {
  lane: DecisionLane;
  showHotspots: boolean;
  onAccept?: ((decision: DecisionRecord) => void) | undefined;
  onDismiss?: ((decision: DecisionRecord) => void) | undefined;
  onReaffirm?: ((decision: DecisionRecord) => void) | undefined;
  readOnlyReason?: string | undefined;
  pendingIds?: ReadonlySet<string> | undefined;
  selection?: Selection | undefined;
}


const CHECKBOX_CLS = "h-3.5 w-3.5 cursor-pointer accent-[var(--color-accent-primary)]";

function buildColumns(ctx: ColumnContext): ResponsiveColumn<DecisionRecord>[] {
  const columns: ResponsiveColumn<DecisionRecord>[] = [];
  const sel = ctx.selection;
  if (sel) {
    columns.push({
      key: "select",
      header: (
        <input
          type="checkbox"
          aria-label="Select every candidate on this page"
          checked={sel.allSelected}
          disabled={sel.busy}
          onChange={sel.toggleAll}
          className={CHECKBOX_CLS}
        />
      ),
      priority: 1,
      hideInCard: true,
      cellClassName: "w-8 align-top",
      render: (d) => (
        // Its own target: ticking a row must not also open the drawer.
        <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            aria-label={`Select ${stripMarkdown(d.title)}`}
            checked={sel.selected.has(d.id)}
            disabled={sel.busy}
            onChange={() => sel.toggle(d.id)}
            className={CHECKBOX_CLS}
          />
        </span>
      ),
    });
  }
  columns.push(
    {
      key: "decision",
      header: "Decision",
      priority: 1,
      cellClassName: "min-w-[240px] align-top",
      render: renderDecisionCell,
    },
    {
      key: "evidence",
      header: "Evidence",
      priority: 2,
      cellClassName: "w-[30%] max-w-[320px] align-top",
      render: renderEvidenceCell,
    },
    {
      key: "scope",
      header: "Scope",
      priority: 3,
      cellClassName: "max-w-[200px] align-top",
      render: renderScopeCell,
    },
  );
  // Drawn when the host sent the field at all, not when a row has a hotspot:
  // a column that came and went with the page under it would read as a
  // property of the page.
  if (ctx.showHotspots) {
    columns.push({
      key: "hotspots",
      header: "Hotspots",
      align: "right",
      priority: 2,
      cellClassName: "align-top",
      render: renderHotspotCell,
    });
  }
  columns.push(
    {
      key: "changed",
      // The claim the number supports, as on the full table: the numerator
      // also counts files the repository no longer tracks.
      header: "Scope changed",
      mobileLabel: "Changed",
      align: "right",
      priority: 3,
      cellClassName: "align-top",
      render: renderChangedCell,
    },
    {
      key: "age",
      header: "Recorded",
      align: "right",
      priority: 3,
      cellClassName: "align-top",
      render: (d) => (
        <span
          className={cn(NUMERIC_CELL, "text-[var(--color-text-tertiary)]")}
          title={parseDateOrNull(d.created_at) ? formatDate(d.created_at) : undefined}
        >
          {formatRelativeTimeOrNull(d.created_at)}
        </span>
      ),
    },
  );
  // One primary verb per row, and only in the lane where it means something.
  // Everything else on the record lives on its detail page.
  if (ctx.lane === "candidates" || (ctx.lane === "needs_review" && ctx.onReaffirm)) {
    columns.push({
      key: "actions",
      header: <span className="sr-only">Review</span>,
      align: "right",
      priority: 1,
      cellClassName: "align-top",
      render: (d) => <RowActions d={d} ctx={ctx} />,
    });
  }
  return columns;
}

function renderDecisionCell(d: DecisionRecord) {
  const currency = d.currency ?? null;
  return (
    <div className="min-w-0 space-y-0.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {/* Not truncated: the title is the primary label of an attention row. */}
        <span className="font-medium text-[var(--color-text-primary)]">
          {stripMarkdown(d.title)}
        </span>
        {/* Marked only where it is an exception. An accepted decision that
            still describes its code is the quiet default and carries no
            badge; a currency the reader cannot guess does. */}
        {currency !== null && currency !== "active" && (
          <CurrencyMark currency={currency as DecisionCurrency} />
        )}
        {d.verification && d.verification !== "exact" && (
          <VerificationBadge verification={d.verification} iconOnly />
        )}
      </div>
      <p className="line-clamp-2 text-xs text-[var(--color-text-secondary)]">
        {stripMarkdown(d.decision || d.title)}
      </p>
    </div>
  );
}

/**
 * The quote a candidate was drawn from. It is the whole basis for accepting or
 * dismissing one, so it stays on the row, clamped; the drawer has it in full.
 */
function renderEvidenceCell(d: DecisionRecord) {
  const evidence = d.evidence_preview;
  if (!evidence?.source_quote) {
    return <span className={EVIDENCE_META}>{decisionSourceLabel(d.source)}</span>;
  }
  return (
    <figure className="min-w-0 space-y-0.5">
      <blockquote className="line-clamp-2 text-xs italic text-[var(--color-text-secondary)]">
        &ldquo;{stripMarkdown(evidence.source_quote)}&rdquo;
      </blockquote>
      <figcaption className={cn(EVIDENCE_META, "break-all")}>
        {decisionSourceLabel(evidence.source)}
        {evidence.evidence_file ? ` · ${evidence.evidence_file}` : ""}
        {evidence.evidence_line != null ? `:${evidence.evidence_line}` : ""}
        {(d.evidence_count ?? 0) > 1 ? ` · ${(d.evidence_count ?? 0) - 1} more` : ""}
      </figcaption>
    </figure>
  );
}

function renderScopeCell(d: DecisionRecord) {
  const scope = [...d.affected_files, ...d.affected_modules];
  // An agreement governs the repository rather than part of it, so naming no
  // file is what it is, not a gap in the record. One path reads as the path;
  // several read as a count with the list on the title and in the drawer.
  const label =
    d.kind === "agreement" && scope.length === 0
      ? DECISION_KIND_LABELS.agreement.toLowerCase()
      : scope.length === 0
        ? "names nothing"
        : scope.length === 1
          ? (scope[0] as string)
          : `${scope.length} paths`;
  // A path may break anywhere; a count must not split mid-word.
  return (
    <span
      className={cn(
        "block font-mono text-xs text-[var(--color-text-secondary)]",
        scope.length === 1 ? "break-all" : "whitespace-nowrap",
      )}
      title={scope.join(", ")}
    >
      {label}
    </span>
  );
}

function renderHotspotCell(d: DecisionRecord) {
  const hot = d.hotspot_files ?? [];
  return (
    <span
      className={cn(
        NUMERIC_CELL,
        hot.length > 0 ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-tertiary)]",
      )}
      title={hot.length > 0 ? `Churn hotspots it names: ${hot.join(", ")}` : "Names no churn hotspot"}
    >
      {hot.length > 0 ? hot.length : "–"}
    </span>
  );
}

function renderChangedCell(d: DecisionRecord) {
  const s = describeRecordStaleness(d);
  return (
    <span
      className={cn(
        NUMERIC_CELL,
        s.kind === "moved" ? "text-[var(--color-text-secondary)]" : "text-[var(--color-text-tertiary)]",
      )}
      title={s.sentence}
    >
      {s.short}
    </span>
  );
}

/** The row is clickable, so a verb must not also open the drawer. */
const stop = (e: React.SyntheticEvent) => e.stopPropagation();

function ReaffirmAction({
  d,
  pending,
  onReaffirm,
}: {
  d: DecisionRecord;
  pending: boolean;
  onReaffirm: (decision: DecisionRecord) => void;
}) {
  return (
    <div className="flex justify-end" onClick={stop} onKeyDown={stop}>
      <Button
        variant="secondary"
        size="sm"
        disabled={pending}
        title="Still right after re-reading it: mark it current again"
        onClick={() => onReaffirm(d)}
      >
        {pending ? "Reaffirming..." : "Reaffirm"}
      </Button>
    </div>
  );
}

function RowActions({ d, ctx }: { d: DecisionRecord; ctx: ColumnContext }) {
  const { onAccept, onDismiss, readOnlyReason } = ctx;
  const pending = ctx.pendingIds?.has(d.id) ?? false;
  if (readOnlyReason) {
    return <span className="text-xs text-[var(--color-text-tertiary)]">{readOnlyReason}</span>;
  }
  if (ctx.lane === "needs_review") {
    return ctx.onReaffirm ? <ReaffirmAction d={d} pending={pending} onReaffirm={ctx.onReaffirm} /> : null;
  }
  if (!onAccept && !onDismiss) return null;
  // The same checks the engine refuses on, asked before the button is drawn,
  // so a candidate that names nothing does not sit beside an Accept that
  // could only fail.
  const blockers = decisionAcceptanceBlockers(d);
  return (
    <div className="flex flex-col items-end gap-1" onClick={stop} onKeyDown={stop}>
      <div className="flex items-center justify-end gap-1.5">
        {onDismiss && (
          <Button variant="ghost" size="sm" disabled={pending} onClick={() => onDismiss(d)}>
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
      {/* Text, not only a tooltip: a disabled button takes no focus. Dismiss
          stays available, because a candidate that cannot be accepted is
          exactly the one worth tombstoning. The drawer says how to fill it. */}
      {onAccept && blockers.length > 0 && (
        <p className="max-w-[24ch] text-right text-xs text-[var(--color-text-tertiary)]">
          Cannot accept: {blockers.join("; ")}.
        </p>
      )}
    </div>
  );
}

/**
 * Who signed the record's current authority, by name, for the drawer only.
 *
 * Provenance and nothing else: it names the signer and the session without
 * sorting signers into kinds, so it reads the same whoever signed. A candidate
 * has no signer and renders nothing; its source is already in the header.
 */
function Provenance({ decision }: { decision: DecisionRecord }) {
  if (decision.currency == null) return null;
  const who = decision.accepter?.trim();
  const session = decision.accepter_session?.trim();
  return (
    <span className="text-xs text-[var(--color-text-tertiary)]">
      {who ? `Signed by ${who}` : "Signer not recorded"}
      {who && session ? `, session ${session}` : ""}
    </span>
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
      className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-[var(--color-text-tertiary)]"
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

/**
 * The selection's verb and its outcome. Dismiss only: accepting makes a record
 * govern what agents are told, and that is read one record at a time.
 */
function BulkBar({
  selected,
  bulk,
  onDismiss,
  onClear,
}: {
  selected: number;
  bulk: BulkState | null;
  onDismiss: () => void;
  onClear: () => void;
}) {
  const running = bulk !== null && bulk.result === null;
  const result = bulk?.result ?? null;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-y border-[var(--color-border-subtle)] bg-[var(--color-bg-inset)] px-3 py-2 text-xs"
    >
      {running ? (
        <span className="tabular-nums text-[var(--color-text-secondary)]">
          Dismissing {bulk.settled} of {bulk.total}...
        </span>
      ) : (
        <>
          {result && (
            <span className="text-[var(--color-text-secondary)]">
              Dismissed {result.done.length} of {bulk?.total}.
              {result.failed.length > 0 &&
                ` ${result.failed.length} refused and still selected: ${result.failed
                  .map((f) => `${stripMarkdown(f.decision.title)} (${f.message})`)
                  .join("; ")}.`}
            </span>
          )}
          {selected > 0 && (
            <>
              <span className="tabular-nums text-[var(--color-text-secondary)]">
                {selected} selected
              </span>
              <Button variant="secondary" size="sm" onClick={onDismiss}>
                Dismiss {selected}
              </Button>
            </>
          )}
          <Button variant="ghost" size="sm" onClick={onClear}>
            {selected > 0 ? "Clear" : "Done"}
          </Button>
        </>
      )}
    </div>
  );
}
