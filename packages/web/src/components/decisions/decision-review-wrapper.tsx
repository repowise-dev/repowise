"use client";

import * as React from "react";
import useSWR, { useSWRConfig } from "swr";
import Link from "next/link";
import { toast } from "sonner";
import {
  DecisionRecordActions,
  DecisionReviewLanes,
  type DecisionRecordPromptKind,
  type DecisionSection,
} from "@repowise-dev/ui/decisions/decision-review-lanes";
import { DecisionDigest } from "@repowise-dev/ui/decisions/decision-digest";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { AiPromptButton } from "@repowise-dev/ui/health/ai-prompt-button";
import type { DecisionLaneCounts, DecisionRecord } from "@repowise-dev/types/decisions";
import {
  getDecisionAgentPrompt,
  getDecisionHealth,
  getDecisionLaneCounts,
  listDecisions,
  patchDecision,
  reaffirmDecision,
  setDecisionPinned,
} from "@/lib/api/decisions";
import { reviewEach } from "@repowise-dev/ui/decisions";
import { DecisionRecordPrompts, useDecisionAgentPrompt } from "./add-decision-button";
import { useTranslations } from "next-intl";
import { formatNumber, stripMarkdown } from "@repowise-dev/ui/lib/format";

/** Rows the digest's This week shows before pointing at the Recent section. */
const RECENT_DIGEST_ROWS = 5;

interface Props {
  repoId: string;
  pageSize?: number;
  /** The server's lane-count read, so the badges have numbers on first paint. */
  initialLaneCounts?: DecisionLaneCounts | undefined;
  /** False on a store with no records: the digest and the list have nothing to show. */
  hasRecords?: boolean;
}

/** Section badges from the lane counts. A count the server did not send stays unknown. */
function sectionCounts(counts: DecisionLaneCounts): Partial<Record<DecisionSection, number>> {
  const out: Partial<Record<DecisionSection, number>> = {
    candidates: counts.candidates,
    history: counts.history,
  };
  if (counts.in_force != null) out.in_force = counts.in_force;
  if (counts.recent != null) out.recent = counts.recent;
  return out;
}

/**
 * Records per lane, under the one key every reader on the page shares.
 *
 * The lede and the tab badges both read this, and the review refresh
 * revalidates every key under `/api/repos/{id}/decisions`, so after a write
 * the headline and the In force badge move together.
 */
export function useDecisionLaneCounts(
  repoId: string,
  fallbackData?: DecisionLaneCounts | undefined,
) {
  return useSWR(
    [`/api/repos/${repoId}/decisions/lane-counts`],
    () => getDecisionLaneCounts(repoId),
    fallbackData ? { fallbackData } : undefined,
  );
}

/**
 * The digest and the full record under one piece of state, so "Review 222" in
 * the digest can open In force narrowed to the decisions whose code moved.
 */
export function DecisionsBoard({
  repoId,
  pageSize = 50,
  initialLaneCounts,
  hasRecords = true,
}: Props) {
  const t = useTranslations("decisions");
  const tv = useTranslations("views.decisions");
  // Opens on what is in force. Background records are context, not work, so
  // the page never opens on them; the reader picks that section.
  const [lane, setLane] = React.useState<DecisionSection>("in_force");
  // In force narrowed to the `needs_review` lane: accepted, and the code they
  // name changed since. The server serves that lane; this only asks for it.
  const [movedOnly, setMovedOnly] = React.useState(false);
  const listRef = React.useRef<HTMLDivElement>(null);
  const add = useDecisionAgentPrompt(repoId, "add");

  // One aggregate over the acceptance join, not four page fetches. The badges
  // and the rows they label therefore come from the same question.
  //
  // Deliberately not /decisions/counts: that endpoint groups the status
  // column, which is the projection rather than the authority, so its
  // "active" would contradict the lane a reader then opened.
  const { data: counts } = useDecisionLaneCounts(repoId, initialLaneCounts);
  // The digest's ungoverned-hotspots row: churn hotspots no decision in force
  // names. Under the decisions prefix so an accept that starts governing a
  // hotspot drops it from the row without a reload. A failure only drops the
  // row. Ceiling: the rollup also ships the stale and background lists this
  // page never shows; upgrade path is a counts-only health read.
  const { data: health } = useSWR(
    hasRecords ? [`/api/repos/${repoId}/decisions/health`] : null,
    () => getDecisionHealth(repoId),
  );
  const { data: recent } = useSWR(
    hasRecords ? [`/api/repos/${repoId}/decisions`, "digest", "recent", RECENT_DIGEST_ROWS] : null,
    () => listDecisions(repoId, { lane: "recent", include_proposed: true, limit: RECENT_DIGEST_ROWS }),
  );

  // A review moves a record between lanes and changes what the prompts list,
  // so every decisions read on the page revalidates together: rows, badges,
  // the digest and the discovery prompt.
  const { mutate } = useSWRConfig();
  const refresh = React.useCallback(
    () =>
      mutate(
        (key) =>
          Array.isArray(key) &&
          typeof key[0] === "string" &&
          key[0].startsWith(`/api/repos/${repoId}/decisions`),
      ),
    [mutate, repoId],
  );
  const writes = useReviewWrites(repoId, refresh);
  const loadRecordPrompt = React.useCallback(
    (d: DecisionRecord, kind: DecisionRecordPromptKind) =>
      getDecisionAgentPrompt(repoId, kind, d.id).then((r) => r.text),
    [repoId],
  );

  const showList = (next: DecisionSection, moved: boolean) => {
    setLane(next);
    setMovedOnly(moved);
    listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  if (!hasRecords) return null;

  return (
    <>
      <DecisionDigest
        needsReview={counts?.needs_review}
        onReviewNeedsReview={() => showList("in_force", true)}
        ungovernedHotspots={health?.ungoverned_hotspots}
        ungovernedAction={
          add?.text ? (
            <AiPromptButton variant="ghost" copyText={() => add.text} label={t("hero.recordMissed")} />
          ) : undefined
        }
        recent={recent}
        recentTotal={counts?.recent}
        onShowAllRecent={() => showList("recent", false)}
        recentHref={(d) => `/repos/${repoId}/decisions/${d.id}`}
        LinkComponent={Link}
        renderRecentActions={(d) => (
          <DecisionRecordActions
            decision={d}
            section="recent"
            onPin={(rec, pinned) => void writes.review(rec, pinned ? "pin" : "unpin")}
            onReaffirm={(rec) => void writes.review(rec, "reaffirm")}
            loadRecordPrompt={loadRecordPrompt}
            pending={writes.pending.has(d.id)}
            href={`/repos/${repoId}/decisions/${d.id}`}
            LinkComponent={Link}
          />
        )}
      />
      <div ref={listRef} className="scroll-mt-6">
        <OverviewSection title={tv("reviewTitle")} description={tv("reviewDescription")}>
          <DecisionReviewWrapper
            repoId={repoId}
            pageSize={pageSize}
            lane={lane}
            onLaneChange={(next) => {
              setLane(next);
              setMovedOnly(false);
            }}
            movedOnly={movedOnly && lane === "in_force"}
            onClearMoved={() => setMovedOnly(false)}
            counts={counts}
            writes={writes}
            loadRecordPrompt={loadRecordPrompt}
          />
        </OverviewSection>
      </div>
    </>
  );
}

/**
 * The host half of the decisions sections: the per-section fetch, the paging
 * and the wiring of the writes.
 *
 * The lane is a server query parameter rather than a client-side filter of a
 * loaded page. A live index carries 381 candidates against 122 accepted
 * records, so filtering a fifty-row window would show an empty Active lane on
 * a repository with a hundred rules in it.
 */
function DecisionReviewWrapper({
  repoId,
  pageSize,
  lane,
  onLaneChange,
  movedOnly,
  onClearMoved,
  counts,
  writes,
  loadRecordPrompt,
}: {
  repoId: string;
  pageSize: number;
  lane: DecisionSection;
  onLaneChange: (lane: DecisionSection) => void;
  movedOnly: boolean;
  onClearMoved: () => void;
  counts: DecisionLaneCounts | undefined;
  writes: ReturnType<typeof useReviewWrites>;
  loadRecordPrompt: (d: DecisionRecord, kind: DecisionRecordPromptKind) => Promise<string>;
}) {
  const t = useTranslations("decisions");
  const [page, setPage] = React.useState(0);
  const [source, setSource] = React.useState("all");

  // Background is ranked by `sort=review` (what is worth a look first; see
  // `review_queue_order` in core). In force comes pinned first, recent newest
  // first, both ordered by the server.
  const background = lane === "candidates";
  const serverLane = movedOnly ? "needs_review" : lane;
  // Either control invalidates the offset: page 3 of Background is not page 3
  // of In force, and leaving it put lands the reader on an empty lane. Reset
  // while rendering, so the old offset is never fetched for the new lane.
  const [pagedLane, setPagedLane] = React.useState(serverLane);
  if (pagedLane !== serverLane) {
    setPagedLane(serverLane);
    setPage(0);
  }

  const { data, error, mutate, isLoading } = useSWR(
    [`/api/repos/${repoId}/decisions`, "lane", serverLane, source, page, pageSize],
    () =>
      listDecisions(repoId, {
        lane: serverLane,
        ...(source !== "all" ? { source } : {}),
        include_proposed: true,
        limit: pageSize,
        offset: page * pageSize,
        ...(background ? { sort: "review" as const } : {}),
      }),
  );

  const changeSource = React.useCallback((next: string) => {
    setSource(next);
    setPage(0);
  }, []);

  const rows = data ?? [];

  // Reviewing the last row of a later page leaves that page empty. Step back
  // instead of showing "Nothing in background" over a lane that still has rows.
  React.useEffect(() => {
    if (page > 0 && data !== undefined && data.length === 0) {
      setPage((p) => Math.max(0, p - 1));
    }
  }, [page, data]);

  // Only when the rows and the badge count the same set. Under a source filter
  // the lane count is the wrong denominator, so the range says what it loaded
  // and nothing more rather than "1-12 of 381".
  const badges = counts ? sectionCounts(counts) : undefined;
  const laneTotal = movedOnly ? counts?.needs_review : badges?.[lane];
  const total = source === "all" ? laneTotal : undefined;
  const first = page * pageSize + 1;
  const last = page * pageSize + rows.length;
  const hasNext = total !== undefined ? last < total : rows.length === pageSize;
  const { pending, review, dismissMany } = writes;

  return (
    <div className="space-y-4">
      <DecisionReviewLanes
        lane={lane}
        onLaneChange={onLaneChange}
        {...(background ? { order: "review" as const } : {})}
        decisions={data}
        {...(badges ? { counts: badges } : {})}
        source={source}
        onSourceChange={changeSource}
        onAccept={(d, options) => void review(d, "accept", options?.reason)}
        onDismiss={(d) => void review(d, "dismiss")}
        onReaffirm={(d) => void review(d, "reaffirm")}
        onPin={(d, pinned) => void review(d, pinned ? "pin" : "unpin")}
        recordPrompts={(d) => <DecisionRecordPrompts repoId={repoId} decision={d} />}
        loadRecordPrompt={loadRecordPrompt}
        onDismissMany={dismissMany}
        {...(movedOnly
          ? {
              filterNote: (
                <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span>
                    {t("board.movedOnly", { count: counts?.needs_review ?? rows.length })}
                  </span>
                  <button
                    type="button"
                    onClick={onClearMoved}
                    className="rounded text-[var(--color-accent-primary)] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
                  >
                    {t("board.showAll")}
                  </button>
                </p>
              ),
            }
          : {})}
        pendingIds={pending}
        repoId={repoId}
        LinkComponent={Link}
        error={error}
        isLoading={isLoading}
        onRetry={() => void mutate()}
      />

      {/* Paged, because a live index carries 381 candidates against a 50-row
          window. The denominator is the lane's own measured count, so the
          range and the badge above it come from one number. */}
      {(page > 0 || hasNext) && (
        <div className="flex items-center justify-between gap-4 border-t border-[var(--color-border-default)] pt-3">
          <p className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
            {rows.length > 0 ? `${first}–${last}` : "0"}
            {total !== undefined ? ` of ${formatNumber(total)}` : ""}
          </p>
          <div className="flex items-center gap-2">
            <PageButton
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0 || isLoading}
            >
              {t("review.previous")}
            </PageButton>
            <PageButton onClick={() => setPage((p) => p + 1)} disabled={!hasNext || isLoading}>
              {t("review.next")}
            </PageButton>
          </div>
        </div>
      )}
    </div>
  );
}

type ReviewVerb = "accept" | "dismiss" | "reaffirm" | "pin" | "unpin";

/** The toast for each verb that went through, and for each that was refused. */
const DONE = {
  accept: "review.accepted",
  dismiss: "review.dismissed",
  reaffirm: "review.reaffirmed",
  pin: "review.pinned",
  unpin: "review.unpinned",
} as const satisfies Record<ReviewVerb, string>;
const FAILED = {
  accept: "review.acceptFailed",
  dismiss: "review.dismissFailed",
  reaffirm: "review.reaffirmFailed",
  pin: "review.pinFailed",
  unpin: "review.unpinFailed",
} as const satisfies Record<ReviewVerb, string>;

/**
 * The writes and their in-flight set: one record at a time for the row and
 * drawer verbs, a capped loop for a selection.
 */
function useReviewWrites(repoId: string, refresh: () => Promise<unknown>) {
  const t = useTranslations("decisions");
  // A set, not one id: two accepts can be in flight, and a single value let
  // the first to resolve re-enable the second row's buttons while its own
  // write was still running, so a second click double-wrote.
  const [pending, setPending] = React.useState<ReadonlySet<string>>(new Set());
  const review = React.useCallback(
    async (d: DecisionRecord, verb: ReviewVerb, reason?: string) => {
      setPending((prev) => new Set(prev).add(d.id));
      try {
        if (verb === "reaffirm") {
          await reaffirmDecision(repoId, d.id);
        } else if (verb === "pin" || verb === "unpin") {
          await setDecisionPinned(repoId, d.id, verb === "pin");
        } else {
          await patchDecision(repoId, d.id, {
            status: verb === "accept" ? "active" : "dismissed",
            ...(verb === "accept" && reason ? { reason } : {}),
          });
        }
        toast.success(t(DONE[verb]));
        // Rows and badges together: a review action moves a record between
        // lanes, so refreshing the rows without the badges leaves every tab
        // count contradicting the list under it.
        await refresh();
      } catch (err) {
        // The acceptance contract refuses a record with no reason, scope or
        // evidence, and it names which. Surface that sentence: it is the whole
        // instruction for what to do next. Titled, because the drawer has
        // already moved to the next record and the reader needs to know which
        // one did not take.
        const message = err instanceof Error ? err.message : null;
        toast.error(stripMarkdown(d.title), {
          description:
            message === null ? t("review.actionFailed") : t(FAILED[verb], { message }),
        });
      } finally {
        setPending((prev) => {
          const next = new Set(prev);
          next.delete(d.id);
          return next;
        });
      }
    },
    [repoId, refresh, t],
  );

  // One dismiss per record through the same write as the row verb. There is
  // no bulk endpoint, and a page holds at most fifty rows, so a capped loop
  // is enough; the lane reports what went through and what was refused.
  const dismissMany = React.useCallback(
    async (decisions: DecisionRecord[], onProgress: (settled: number) => void) => {
      const result = await reviewEach(
        decisions,
        (d) => patchDecision(repoId, d.id, { status: "dismissed" }),
        onProgress,
      );
      if (result.done.length > 0) {
        toast.success(t("review.bulkDismissed", { count: result.done.length }));
      }
      await refresh();
      return result;
    },
    [repoId, refresh, t],
  );
  return { pending, review, dismissMany };
}

function PageButton({
  onClick,
  disabled,
  children,
}: {
  onClick: () => void;
  disabled: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-md border border-[var(--color-border-default)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-40 disabled:hover:border-[var(--color-border-default)] disabled:hover:text-[var(--color-text-secondary)]"
    >
      {children}
    </button>
  );
}
