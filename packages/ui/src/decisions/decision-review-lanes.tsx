"use client";

import * as React from "react";
import {
  DECISION_CURRENCY_DESCRIPTIONS,
  DECISION_CURRENCY_LABELS,
  DECISION_KIND_LABELS,
  DECISION_LANES,
  DECISION_SECTIONS,
  DECISION_SOURCES,
  decisionAcceptanceBlockers,
  decisionSourceLabel,
  isRetiredDecisionSource,
} from "@repowise-dev/types/decisions";
import type {
  DecisionAgentPromptKind,
  DecisionCurrency,
  DecisionLane,
  DecisionRecord,
  DecisionSection,
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
import {
  DecisionReviewDrawer,
  PIN_LABEL,
  PIN_TITLE,
  PUT_IN_FORCE_LABEL,
  PinnedMark,
  UNPIN_LABEL,
  UNPIN_TITLE,
} from "./decision-review-drawer";
import { shortGap, type DecisionAcceptOptions } from "./decision-confirm";
import { DecisionAppliedLine, DecisionOrigin, acceptedLine } from "./decision-origin";
import { Check, ClipboardCopy } from "lucide-react";
import {
  DocsMoreMenu,
  type DocsMenuGroup,
  type DocsMenuItem,
  type DocsMenuLinkComponent,
} from "../docs/docs-more-menu";

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
import type { BulkReviewResult } from "./bulk-review";
import { useBulkSelection, type BulkState, type Selection } from "./use-bulk-selection";
import {
  formatDate,
  formatRelativeTimeOrNull,
  parseDateOrNull,
  stripMarkdown,
} from "../lib/format";

/**
 * The decisions page's sections: what is in force (pinned first), what came
 * into force this week, the mined background, and what has been retired.
 *
 * Not a review queue. Agents put decisions in force when their commits merge;
 * a person reads this as a digest and pins, vetoes or changes a decision,
 * usually by handing the record's prompt to their agent.
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
export type { DecisionLane, DecisionSection };
export { DECISION_LANES, DECISION_SECTIONS };

/** The section names. The same words as `repowise decision status` and the docs. */
export const SECTION_LABEL: Record<DecisionSection, string> = {
  in_force: "In force",
  recent: "Recent",
  candidates: "Background",
  history: "History",
};

/**
 * What the section holds, and what the reader can do with it. Shown under the
 * tab row, because a section whose name is a noun still owes the reader a
 * sentence about why its rows are in it.
 */
const LANE_DESCRIPTION: Record<DecisionSection, string> = {
  in_force:
    "Decisions in force. An agent editing a file one of these names is told about it. Pinned ones come first: a person holds them as law, and an agent asks before replacing one.",
  recent:
    "What your agents decided in the last week, newest first: who put each decision in force, and the commit it landed in.",
  candidates:
    "Mined from pull requests, commits, comments and sessions. Not in force: agents ask about them through get_why for the why behind the code. Hand one to your agent with its prompt, or decide it yourself from its menu.",
  history:
    "Decisions that were in force and then withdrawn, superseded or dismissed, or whose files no longer exist. Kept so the record of what changed survives.",
};

/** An empty section says what will fill it. */
const LANE_EMPTY: Record<DecisionSection, string> = {
  in_force:
    "Nothing is in force yet. A decision an agent records comes into force when its commit merges.",
  recent:
    "Nothing came into force in the last week. A decision an agent records comes into force when its commit merges.",
  candidates: "No background records.",
  history: "Nothing has been retired yet.",
};

/** Sections whose rows are in force, where pin and reaffirm apply. */
const IN_FORCE_SECTIONS: ReadonlySet<DecisionSection> = new Set(["in_force", "recent"]);

/** The background lane's agent action. Hosts use the same words wherever they offer it. */
export const REVIEW_WITH_AGENT_LABEL = "Check with your agent";

/** The agent action for recording decisions nobody wrote down yet. */
export const ADD_WITH_AGENT_LABEL = "Add decisions with your agent";

/** The prompts the server renders about one record. */
export type DecisionRecordPromptKind = Extract<
  DecisionAgentPromptKind,
  "review" | "edit" | "veto" | "pin" | "unpin"
>;

/** The currencies of a decision that has left force. */
const RETIRED_CURRENCIES: ReadonlySet<string> = new Set(["superseded", "dismissed", "stale"]);

/**
 * Which prompts a record gets, primary first: a background record the check;
 * a decision in force change, veto, and pin or unpin; a retired or dismissed
 * one none, since it was settled and checking it again would undo that.
 */
export function recordPromptKinds(
  d: Pick<DecisionRecord, "currency" | "pinned" | "status">,
): DecisionRecordPromptKind[] {
  if (d.currency == null) return d.status === "proposed" ? ["review"] : [];
  if (RETIRED_CURRENCIES.has(d.currency)) return [];
  return ["edit", "veto", d.pinned ? "unpin" : "pin"];
}

/** Each record prompt as a menu item names it. */
export const RECORD_PROMPT_MENU_LABEL: Record<DecisionRecordPromptKind, string> = {
  review: "Copy prompt to check it",
  edit: "Copy prompt to change it",
  veto: "Copy prompt to veto it",
  pin: "Copy prompt to pin it",
  unpin: "Copy prompt to unpin it",
};

/** What a copied record prompt asks, for the status line. */
const RECORD_PROMPT_VERB: Record<DecisionRecordPromptKind, string> = {
  review: "check",
  edit: "change",
  veto: "veto",
  pin: "pin",
  unpin: "unpin",
};

export interface DecisionReviewLanesProps {
  lane: DecisionSection;
  onLaneChange: (lane: DecisionSection) => void;
  /**
   * Rows for the current lane. The caller filters, because the lane is a
   * server query parameter and this shows one page of it.
   */
  decisions: DecisionRecord[] | undefined;
  /**
   * Per-lane totals for the tab badges. A lane whose count is unknown renders
   * without one rather than with a number nobody measured.
   */
  counts?: Partial<Record<DecisionSection, number>>;
  /**
   * Narrow the lane to one capture source, or `"all"`. A separate axis from
   * the lane: the lane asks who accepted a record, this asks where it came
   * from, and the two never select the same thing.
   */
  source?: string;
  onSourceChange?: (source: string) => void;
  /**
   * Accepts the candidate. Omit where the surface cannot write. `options`
   * carries a reason typed in the drawer; send it as `reason` on the write.
   */
  onAccept?: (decision: DecisionRecord, options?: DecisionAcceptOptions) => void;
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
   * Reaffirms a decision in force whose files moved, after re-reading it.
   * Offered on those rows only. Omit where the surface cannot write.
   */
  onReaffirm?: (decision: DecisionRecord) => void;
  /**
   * Pins or unpins a decision in force. Offered on in-force rows. Omit where
   * the surface cannot write.
   */
  onPin?: (decision: DecisionRecord, pinned: boolean) => void;
  /**
   * The agent prompts for one record (pin, veto, change it), shown in its
   * drawer. A render prop, so the host can fetch them for the open record.
   */
  recordPrompts?: (decision: DecisionRecord) => React.ReactNode;
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
  /**
   * @deprecated No longer drawn. The page's discovery prompt leads the page
   * in `DecisionAgentHero`; each row carries its own prompt through
   * `loadRecordPrompt`. Kept so existing hosts compile.
   */
  reviewPrompt?: () => string;
  /**
   * Reads one record's prompt from the server. Given, each row's primary
   * action copies its first prompt (the check for a background record, the
   * change for one in force) and its menu offers the rest. Resolve `null`
   * when the server has none; the row reports that the copy failed.
   */
  loadRecordPrompt?: (
    decision: DecisionRecord,
    kind: DecisionRecordPromptKind,
  ) => Promise<string | null | undefined>;
  /**
   * A line above the rows saying the host narrowed the section (for example
   * to decisions whose code changed), with the way back.
   */
  filterNote?: React.ReactNode;
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
  onPin,
  recordPrompts,
  onDismissMany,
  loadRecordPrompt,
  filterNote,
}: DecisionReviewLanesProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const Link = LinkComponent as LinkLike;
  const [openId, setOpenId] = React.useState<string | null>(null);
  const rows = decisions ?? [];
  // Looked up, not held, so a refresh after a review shows the record
  // as it now is, and a record that left the lane closes the drawer.
  const open = rows.find((d) => d.id === openId) ?? null;
  const reviewable = lane === "candidates";
  const inForce = IN_FORCE_SECTIONS.has(lane);
  const reaffirmable =
    inForce && onReaffirm !== undefined && open?.currency === "needs_review";
  const selectable =
    lane === "candidates" && onDismissMany !== undefined && !readOnlyReason;

  const bulkRun = useBulkSelection(rows, lane, selectable ? onDismissMany : undefined);
  const { selected, bulk, selecting } = bulkRun;
  const copies = useRecordPromptCopy(loadRecordPrompt);
  const listRef = React.useRef<HTMLDivElement>(null);

  // Acting from the drawer moves it to the next record, which is what makes
  // the drawer a queue and not a detour. Read before the host refreshes
  // the rows, because the record acted on is about to leave them.
  const actAndAdvance = (
    act: (decision: DecisionRecord, options?: DecisionAcceptOptions) => void,
    d: DecisionRecord,
    options?: DecisionAcceptOptions,
  ) => {
    const at = rows.findIndex((r) => r.id === d.id);
    const next = rows[at + 1] ?? null;
    if (options) act(d, options);
    else act(d);
    setOpenId(next?.id ?? null);
  };

  const columns = buildColumns({
    lane,
    showHotspots: rows.some((d) => Array.isArray(d.hotspot_files)),
    onAccept,
    onDismiss,
    onReaffirm,
    onPin,
    readOnlyReason,
    pendingIds,
    selection: selectable && selecting ? bulkRun.selection : undefined,
    copyPrompt: loadRecordPrompt ? copies.copy : undefined,
    copiedId: copies.copied,
    openRecord: (d) => setOpenId(d.id),
    recordHref: (d) => `${prefix}/decisions/${d.id}`,
    Link,
    afterRemove: () => requestAnimationFrame(() => listRef.current?.focus()),
  });

  const tabs: ViewTab[] = DECISION_SECTIONS.map((id) => {
    const badge = counts?.[id];
    return badge === undefined
      ? { id, label: SECTION_LABEL[id] }
      : { id, label: SECTION_LABEL[id], badge };
  });

  return (
    <ViewTabs
      tabs={tabs}
      value={lane}
      onValueChange={(id) => onLaneChange(id as DecisionSection)}
    >
      <SectionToolbar
        lane={lane}
        order={order}
        source={source}
        onSourceChange={onSourceChange}
        // Off by default, so the section reads as a list rather than a form.
        selectToggle={
          selectable && rows.length > 0 ? (
            <Button
              variant="outline"
              size="sm"
              aria-pressed={selecting}
              disabled={bulkRun.selection.busy}
              onClick={() => bulkRun.setSelecting(!selecting)}
            >
              {selecting ? "Done selecting" : "Select"}
            </Button>
          ) : null
        }
      />
      {filterNote && (
        <div className="text-xs text-[var(--color-text-secondary)]">{filterNote}</div>
      )}
      <p role="status" aria-live="polite" className="text-xs text-[var(--color-text-secondary)] empty:hidden">
        {copies.note}
      </p>

      {error ? (
        <ApiError
          title="Couldn't load this lane"
          message="An error occurred while fetching decisions."
          {...(onRetry ? { onRetry } : {})}
        />
      ) : rows.length === 0 ? (
        isLoading ? null : <SectionEmpty lane={lane} source={source} />
      ) : (
        <>
          {selectable && selecting && (selected.size > 0 || bulk !== null) && (
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
          <div ref={listRef} tabIndex={-1} className="focus:outline-none">
            <ResponsiveTable
              columns={columns}
              rows={rows}
              rowKey={(d) => d.id}
              onRowClick={(d) => setOpenId(d.id)}
              selectedKey={openId}
              caption={`${SECTION_LABEL[lane]} decisions`}
              bare
              className="border-y border-[var(--color-border-subtle)]"
            />
          </div>
        </>
      )}
      <DecisionReviewDrawer
        decision={open}
        reviewable={reviewable}
        onReaffirm={
          reaffirmable && onReaffirm ? (d) => actAndAdvance(onReaffirm, d) : undefined
        }
        onClose={() => setOpenId(null)}
        onAccept={onAccept ? (d, options) => actAndAdvance(onAccept, d, options) : undefined}
        onDismiss={onDismiss ? (d) => actAndAdvance(onDismiss, d) : undefined}
        onPin={inForce && onPin ? onPin : undefined}
        prompts={open && recordPrompts ? recordPrompts(open) : null}
        readOnlyReason={readOnlyReason}
        pending={open ? (pendingIds?.has(open.id) ?? false) : false}
        href={open ? `${prefix}/decisions/${open.id}` : prefix}
        Link={Link}
        marks={open ? <DrawerMarks decision={open} /> : null}
      />
    </ViewTabs>
  );
}

/**
 * What the section holds, the order the host fetched it in, and its controls:
 * the selection toggle and the source filter.
 */
function SectionToolbar({
  lane,
  order,
  source,
  onSourceChange,
  selectToggle,
}: {
  lane: DecisionSection;
  order: "review" | undefined;
  source: string;
  onSourceChange: ((source: string) => void) | undefined;
  selectToggle: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 max-w-[68ch] flex-1 space-y-1.5 text-xs">
        <p className="text-[var(--color-text-secondary)]">{LANE_DESCRIPTION[lane]}</p>
        {order === "review" && (
          <p className="text-[var(--color-text-tertiary)]">{reviewOrderNote(lane)}</p>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {selectToggle}
        {onSourceChange && <SourceFilter source={source} onSourceChange={onSourceChange} />}
      </div>
    </div>
  );
}

/**
 * Built from the shared source list rather than from the loaded rows: a
 * section page is one window, so a source absent from it would become
 * unselectable, which is the same defect wearing a dynamic coat.
 */
function SourceFilter({
  source,
  onSourceChange,
}: {
  source: string;
  onSourceChange: (source: string) => void;
}) {
  return (
    <select
      value={source}
      onChange={(e) => onSourceChange(e.target.value)}
      aria-label="Filter by source"
      className="shrink-0 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-primary)]"
    >
      <option value="all">All sources</option>
      {DECISION_SOURCES.filter((s) => !isRetiredDecisionSource(s)).map((s) => (
        <option key={s} value={s}>
          {decisionSourceLabel(s)}
        </option>
      ))}
    </select>
  );
}

/** An empty section says what will fill it, or that the filter may be why. */
function SectionEmpty({ lane, source }: { lane: DecisionSection; source: string }) {
  const filtered = source !== "all";
  return (
    <EmptyState
      tone={filtered ? "filtered" : "neutral"}
      title={`Nothing in ${SECTION_LABEL[lane].toLowerCase()}`}
      description={
        filtered
          ? `No ${decisionSourceLabel(source)} record is in this section. The section itself may not be empty; clear the source filter to see it.`
          : LANE_EMPTY[lane]
      }
    />
  );
}

/** The open record's marks in the drawer: pinned, an unusual currency, its signer. */
function DrawerMarks({ decision }: { decision: DecisionRecord }) {
  return (
    <>
      <RecordMarks decision={decision} />
      <Provenance decision={decision} />
    </>
  );
}

/**
 * Pinned, then a currency the reader cannot guess. An accepted decision that
 * still describes its code is the quiet default and carries no badge.
 */
function RecordMarks({ decision: d }: { decision: DecisionRecord }) {
  const currency = d.currency ?? null;
  return (
    <>
      {d.pinned && <PinnedMark />}
      {currency !== null && currency !== "active" && (
        <CurrencyMark currency={currency as DecisionCurrency} />
      )}
    </>
  );
}

/**
 * The rule `review_queue_order` applies on the server, said once above the
 * table, not as a score per row: a number would invite reading the gap
 * between two rows as meaningful, and it is only an order.
 */
function reviewOrderNote(lane: DecisionSection): string {
  // Only background records can be put in force, so only their note names that step.
  return lane === "candidates"
    ? "Records that can be put in force as they stand come first, then those naming churn hotspots, then those whose files have changed most since they were recorded."
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
  lane: DecisionSection;
  showHotspots: boolean;
  onAccept?: ((decision: DecisionRecord, options?: DecisionAcceptOptions) => void) | undefined;
  onDismiss?: ((decision: DecisionRecord) => void) | undefined;
  onReaffirm?: ((decision: DecisionRecord) => void) | undefined;
  onPin?: ((decision: DecisionRecord, pinned: boolean) => void) | undefined;
  readOnlyReason?: string | undefined;
  pendingIds?: ReadonlySet<string> | undefined;
  selection?: Selection | undefined;
  copyPrompt?: ((decision: DecisionRecord, kind: DecisionRecordPromptKind) => void) | undefined;
  /** The row whose prompt was just copied, for its button's acknowledgement. */
  copiedId?: string | null | undefined;
  /** Opens the record in the drawer; without it the menu offers the full page only. */
  openRecord?: ((decision: DecisionRecord) => void) | undefined;
  recordHref: (decision: DecisionRecord) => string;
  Link: LinkLike;
  /**
   * After a verb that takes the row out of the section: focus moves to the
   * list, so it does not fall to the page with the row's menu button.
   */
  afterRemove?: (() => void) | undefined;
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
          aria-label="Select every record on this page"
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
  if (ctx.lane === "recent") columns.push(RECENT_BY_COLUMN);
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
    ageColumn(ctx.lane),
  );
  if (hasRowVerbs(ctx)) {
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

/**
 * One primary verb per row, the record's prompt, and a menu for the rest, in
 * the sections where they mean something. A retired record has no verb: it
 * was settled, and its drawer and detail page hold the record.
 */
function hasRowVerbs(ctx: ColumnContext): boolean {
  if (ctx.lane === "candidates") return true;
  return (
    IN_FORCE_SECTIONS.has(ctx.lane) && Boolean(ctx.onPin || ctx.onReaffirm || ctx.copyPrompt)
  );
}

/** Who put a recent decision in force, and where. */
const RECENT_BY_COLUMN: ResponsiveColumn<DecisionRecord> = {
  key: "by",
  header: "By",
  priority: 2,
  cellClassName: "align-top",
  render: renderRecentByCell,
};

/** A recent row is dated by when it came into force, the rest by when recorded. */
function ageColumn(lane: DecisionSection): ResponsiveColumn<DecisionRecord> {
  const recent = lane === "recent";
  return {
    key: "age",
    header: recent ? "In force" : "Recorded",
    align: "right",
    priority: 3,
    cellClassName: "align-top",
    render: (d) => renderDateCell(recent && d.recent?.at ? d.recent.at : d.created_at),
  };
}

function renderDateCell(at: string) {
  return (
    <span
      className={cn(NUMERIC_CELL, "text-[var(--color-text-tertiary)]")}
      title={parseDateOrNull(at) ? formatDate(at) : undefined}
    >
      {formatRelativeTimeOrNull(at)}
    </span>
  );
}

function renderDecisionCell(d: DecisionRecord) {
  return (
    <div className="min-w-0 space-y-0.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {/* Not truncated: the title is the primary label of an attention row. */}
        <span className="font-medium text-[var(--color-text-primary)]">
          {stripMarkdown(d.title)}
        </span>
        {/* Marked only where it is an exception. */}
        <RecordMarks decision={d} />
        {d.verification && d.verification !== "exact" && (
          <VerificationBadge verification={d.verification} iconOnly />
        )}
        <DecisionOrigin decision={d} />
      </div>
      <p className="line-clamp-2 text-xs text-[var(--color-text-secondary)]">
        {stripMarkdown(d.decision || d.title)}
      </p>
      <DecisionAppliedLine decision={d} />
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

/** Who put a recent decision in force, and the commit it landed in. */
function renderRecentByCell(d: DecisionRecord) {
  const r = d.recent;
  if (!r) return <span className={EVIDENCE_META}>Not recorded</span>;
  const who = r.signer_kind === "agent" ? `${r.signer} (agent)` : r.signer;
  const commit = r.commit ? r.commit.slice(0, 9) : "";
  return (
    <div className="min-w-0 space-y-0.5 text-xs">
      <p className="break-all text-[var(--color-text-secondary)]">{who}</p>
      {commit && (
        <p className={cn(EVIDENCE_META, "font-mono")}>
          {commit}
          {r.landed_on ? ` on ${r.landed_on}` : ""}
        </p>
      )}
    </div>
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

/**
 * The person's own verbs on a background record. Put in force acts at once
 * when the record carries what the contract needs; otherwise it opens the
 * record, where the reason field and the list of gaps are.
 */
function backgroundManualItems(
  d: DecisionRecord,
  ctx: ColumnContext,
  pending: boolean,
): DocsMenuItem[] {
  const items: DocsMenuItem[] = [];
  const { onAccept, onDismiss } = ctx;
  if (onAccept) {
    // The same checks the engine refuses on, asked before the verb is offered.
    const blockers = decisionAcceptanceBlockers(d);
    items.push(
      blockers.length > 0
        ? {
            key: "accept",
            label: `${PUT_IN_FORCE_LABEL}…`,
            description: `${capitalize(blockers.map(shortGap).join("; "))}. Opens the record to fix.`,
            ...(ctx.openRecord
              ? { onSelect: () => ctx.openRecord?.(d) }
              : { href: ctx.recordHref(d) }),
          }
        : {
            key: "accept",
            label: PUT_IN_FORCE_LABEL,
            disabled: pending,
            onSelect: () => {
              onAccept(d);
              ctx.afterRemove?.();
            },
          },
    );
  }
  if (onDismiss) {
    items.push({
      key: "dismiss",
      label: "Dismiss",
      disabled: pending,
      onSelect: () => {
        onDismiss(d);
        ctx.afterRemove?.();
      },
    });
  }
  return items;
}

/** The person's own verbs on a decision in force: pin or unpin, and reaffirm where its files moved. */
function inForceManualItems(
  d: DecisionRecord,
  ctx: ColumnContext,
  pending: boolean,
): DocsMenuItem[] {
  const items: DocsMenuItem[] = [];
  const { onPin } = ctx;
  if (onPin) {
    const pinned = d.pinned === true;
    items.push({
      key: "pin",
      label: pinned ? UNPIN_LABEL : PIN_LABEL,
      description: pinned ? UNPIN_TITLE : PIN_TITLE,
      disabled: pending,
      // The row stays, so focus goes back to its menu button.
      restoresFocus: true,
      onSelect: () => onPin(d, !pinned),
    });
  }
  const reaffirm = d.currency === "needs_review" ? ctx.onReaffirm : undefined;
  if (reaffirm) {
    items.push({
      key: "reaffirm",
      label: "Still holds (reaffirm)",
      description: "Still right after re-reading it: mark it current again",
      disabled: pending,
      restoresFocus: true,
      onSelect: () => reaffirm(d),
    });
  }
  return items;
}

const ROW_COPY_CLS =
  "inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded-md border border-[var(--color-border-default)] px-2 text-xs font-medium text-[var(--color-text-primary)] transition-colors hover:border-[var(--color-border-hover)] hover:bg-[var(--color-action-soft-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

/**
 * Revealed on hover and focus where a pointer can hover, always on touch. A
 * background row keeps it visible: that row is the one asking for work.
 */
const ROW_COPY_REVEAL =
  "opacity-0 focus-visible:opacity-100 group-hover/row:opacity-100 group-focus-within/row:opacity-100 group-data-[selected=true]/row:opacity-100 [@media(hover:none)]:opacity-100";

/**
 * The row's verbs: the record's prompt as the one visible action, and a named
 * menu grouped by who acts. Handing the record to an agent comes first; the
 * person's own verbs are one click deeper, never gone.
 */
function RowActions({ d, ctx }: { d: DecisionRecord; ctx: ColumnContext }) {
  const pending = ctx.pendingIds?.has(d.id) ?? false;
  if (ctx.readOnlyReason) {
    return <span className="text-xs text-[var(--color-text-tertiary)]">{ctx.readOnlyReason}</span>;
  }
  const title = stripMarkdown(d.title);
  const copy = ctx.copyPrompt;
  const kinds = copy ? recordPromptKinds(d) : [];
  const primary = kinds[0];
  const background = ctx.lane === "candidates";
  const manual = background
    ? backgroundManualItems(d, ctx, pending)
    : inForceManualItems(d, ctx, pending);
  const groups: DocsMenuGroup[] = [
    {
      key: "agent",
      label: "Hand to your agent",
      items: copy
        ? kinds.map((kind) => ({
            key: `prompt-${kind}`,
            label: RECORD_PROMPT_MENU_LABEL[kind],
            restoresFocus: true,
            onSelect: () => copy(d, kind),
          }))
        : [],
    },
    {
      key: "manual",
      label: background ? "Decide it yourself" : "Do it yourself",
      items: manual,
    },
    {
      key: "record",
      items: [
        ...(ctx.openRecord
          ? [{ key: "open", label: "Read the record here", onSelect: () => ctx.openRecord?.(d) }]
          : []),
        { key: "page", label: "Open and edit the full record", href: ctx.recordHref(d) },
      ],
    },
  ];
  const copied = primary !== undefined && ctx.copiedId === d.id;
  return (
    // React bubbles through the menu's portal, so this also keeps a menu
    // item's click from reaching the row.
    <div className="flex items-center justify-end gap-1" onClick={stop} onKeyDown={stop}>
      {pending && (
        <span className="whitespace-nowrap text-xs text-[var(--color-text-tertiary)]">Saving…</span>
      )}
      {copy && primary && (
        <button
          type="button"
          onClick={() => copy(d, primary)}
          aria-label={`${RECORD_PROMPT_MENU_LABEL[primary]}: ${title}`}
          className={cn(ROW_COPY_CLS, !background && !copied && ROW_COPY_REVEAL)}
        >
          {copied ? (
            <Check aria-hidden className="h-3.5 w-3.5 text-[var(--color-success)]" />
          ) : (
            <ClipboardCopy aria-hidden className="h-3.5 w-3.5" />
          )}
          {copied ? "Copied" : "Copy prompt"}
        </button>
      )}
      <DocsMoreMenu
        iconOnly
        label={`More actions for ${title}`}
        groups={groups}
        LinkComponent={ctx.Link as unknown as DocsMenuLinkComponent}
      />
    </div>
  );
}

export interface DecisionRecordActionsProps {
  decision: DecisionRecord;
  /** The section the record is shown under; it decides which verbs apply. */
  section: DecisionSection;
  onAccept?: (decision: DecisionRecord, options?: DecisionAcceptOptions) => void;
  onDismiss?: (decision: DecisionRecord) => void;
  onReaffirm?: (decision: DecisionRecord) => void;
  onPin?: (decision: DecisionRecord, pinned: boolean) => void;
  loadRecordPrompt?: DecisionReviewLanesProps["loadRecordPrompt"];
  pending?: boolean;
  readOnlyReason?: string;
  /** The record's full page. */
  href: string;
  LinkComponent?: DecisionReviewLanesProps["LinkComponent"];
}

/**
 * One record's verbs outside the table, the same prompt button and menu a
 * table row carries, for a list such as the digest's This week.
 */
export function DecisionRecordActions({
  decision,
  section,
  onAccept,
  onDismiss,
  onReaffirm,
  onPin,
  loadRecordPrompt,
  pending = false,
  readOnlyReason,
  href,
  LinkComponent = "a",
}: DecisionRecordActionsProps) {
  const copies = useRecordPromptCopy(loadRecordPrompt);
  const ctx: ColumnContext = {
    lane: section,
    showHotspots: false,
    onAccept,
    onDismiss,
    onReaffirm,
    onPin: IN_FORCE_SECTIONS.has(section) ? onPin : undefined,
    readOnlyReason,
    pendingIds: pending ? new Set([decision.id]) : undefined,
    copyPrompt: loadRecordPrompt ? copies.copy : undefined,
    copiedId: copies.copied,
    recordHref: () => href,
    Link: LinkComponent as LinkLike,
  };
  return (
    <>
      <RowActions d={decision} ctx={ctx} />
      <span role="status" aria-live="polite" className="sr-only">
        {copies.note}
      </span>
    </>
  );
}

/**
 * Copies one record's prompt, which the host reads from the server, and says
 * so on the status line. The write starts inside the click with the text
 * still on its way, because Safari only lets a page write the clipboard
 * during the gesture; a browser without promised items takes the plain write.
 */
function useRecordPromptCopy(load: DecisionReviewLanesProps["loadRecordPrompt"]) {
  const [note, setNote] = React.useState("");
  const [copied, setCopied] = React.useState<string | null>(null);
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  // The latest copy wins: a slower earlier read must not report over a later
  // one, or the line would name a record the clipboard does not hold.
  const latest = React.useRef(0);
  React.useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const copy = React.useCallback(
    (d: DecisionRecord, kind: DecisionRecordPromptKind) => {
      if (!load) return;
      const title = stripMarkdown(d.title);
      const run = ++latest.current;
      void copyPending(load(d, kind)).then((ok) => {
        if (run !== latest.current) return;
        setCopied(ok ? d.id : null);
        setNote(
          ok
            ? `Copied the prompt to ${RECORD_PROMPT_VERB[kind]} "${title}". Paste it into your agent.`
            : `Couldn't copy the prompt for "${title}".`,
        );
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => {
          setCopied(null);
          setNote("");
        }, 4000);
      });
    },
    [load],
  );
  return { copy, copied, note };
}

async function copyPending(pending: Promise<string | null | undefined>): Promise<boolean> {
  const text = pending.then((t) => {
    if (!t) throw new Error("No prompt for this record.");
    return t;
  });
  // Observed here so a refused read never surfaces as an unhandled rejection.
  text.catch(() => undefined);
  try {
    if (typeof ClipboardItem !== "undefined" && typeof navigator.clipboard?.write === "function") {
      try {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/plain": text.then((t) => new Blob([t], { type: "text/plain" })),
          }),
        ]);
        return true;
      } catch {
        // No promised clipboard items here: the plain write below. Safari has
        // let go of the gesture by then, so it reports false there.
      }
    }
    await navigator.clipboard.writeText(await text);
    return true;
  } catch {
    return false;
  }
}

/**
 * Who signed the record's current authority, by name, for the drawer only.
 *
 * Provenance and nothing else: it names the signer and the session without
 * sorting signers into kinds, so it reads the same whoever signed. A candidate
 * has no signer and renders nothing; its source is already in the header.
 * Nor does a record whose accepter the drawer body already names ("Accepted
 * by your agent", "Accepted by @x"), so the signer is said once.
 */
function Provenance({ decision }: { decision: DecisionRecord }) {
  if (decision.currency == null || acceptedLine(decision) !== null) return null;
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
