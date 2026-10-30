"use client";

import * as React from "react";
import useSWR from "swr";
import Link from "next/link";
import { toast } from "sonner";
import {
  DecisionReviewLanes,
  type DecisionLane,
} from "@repowise-dev/ui/decisions/decision-review-lanes";
import type {
  DecisionLaneCounts,
  DecisionRecord,
} from "@repowise-dev/types/decisions";
import {
  getDecisionLaneCounts,
  listDecisions,
  patchDecision,
  reaffirmDecision,
} from "@/lib/api/decisions";
import { reviewEach } from "@repowise-dev/ui/decisions";
import { useTranslations } from "next-intl";
import { formatNumber, stripMarkdown } from "@repowise-dev/ui/lib/format";

interface Props {
  repoId: string;
  pageSize?: number;
}

/**
 * The host half of the review lanes: the per-lane fetch, the review writes,
 * and the refresh.
 *
 * The lane is a server query parameter rather than a client-side filter of a
 * loaded page. A live index carries 381 candidates against 122 accepted
 * records, so filtering a fifty-row window would show an empty Active lane on
 * a repository with a hundred rules in it.
 */
export function DecisionReviewWrapper({ repoId, pageSize = 50 }: Props) {
  const t = useTranslations("decisions");
  // Null until the reader picks a lane, so the section can open on the queue.
  const [picked, setPicked] = React.useState<DecisionLane | null>(null);
  const [page, setPage] = React.useState(0);
  const [source, setSource] = React.useState("all");

  // One aggregate over the acceptance join, not four page fetches. The badges
  // and the rows they label therefore come from the same question, and a lane
  // holding more rows than a page can carry still reports a measured total.
  //
  // Deliberately not /decisions/counts: that endpoint groups the status
  // column, which is the projection rather than the authority, so its
  // "active" would contradict the lane a reader then opened.
  const {
    data: counts,
    error: countsError,
    mutate: mutateCounts,
  } = useSWR([`/api/repos/${repoId}/decisions/lane-counts`], () =>
    getDecisionLaneCounts(repoId),
  );

  // Until the reader picks one, the section opens on the work.
  const lane: DecisionLane | null = picked ?? openingLane(counts, countsError);

  // Pinned once chosen: a review that empties Candidates refreshes the counts,
  // and a lane still derived from them would move the reader to another tab
  // in the middle of working this one.
  React.useEffect(() => {
    if (picked === null && lane !== null) setPicked(lane);
  }, [picked, lane]);

  // `sort=review` ranks the whole lane by what most needs a person before the
  // page is cut; see `review_queue_order` in core.
  const { data, error, mutate, isLoading } = useSWR(
    lane === null
      ? null
      : [`/api/repos/${repoId}/decisions`, "lane", lane, source, page, pageSize],
    () =>
      listDecisions(repoId, {
        lane: lane as DecisionLane,
        ...(source !== "all" ? { source } : {}),
        include_proposed: true,
        limit: pageSize,
        offset: page * pageSize,
        sort: "review",
      }),
  );

  // A lane change invalidates the offset: page 3 of Candidates is not page 3
  // of Active, and leaving it put lands the reader on an empty lane.
  // Either control invalidates the offset: page 3 of Candidates is not page 3
  // of Active, and leaving it put lands the reader on an empty lane.
  const changeLane = React.useCallback((next: DecisionLane) => {
    setPicked(next);
    setPage(0);
  }, []);

  const changeSource = React.useCallback((next: string) => {
    setSource(next);
    setPage(0);
  }, []);

  const refresh = React.useCallback(
    () => Promise.all([mutate(), mutateCounts()]),
    [mutate, mutateCounts],
  );
  const { pending, review, dismissMany } = useReviewWrites(repoId, refresh);

  const rows = data ?? [];

  // Reviewing the last row of a later page leaves that page empty. Step back
  // instead of showing "Nothing in candidates" over a lane that still has rows.
  React.useEffect(() => {
    if (page > 0 && data !== undefined && data.length === 0) {
      setPage((p) => Math.max(0, p - 1));
    }
  }, [page, data]);

  // Only when the rows and the badge count the same set. Under a source filter
  // the lane count is the wrong denominator, so the range says what it loaded
  // and nothing more rather than "1-12 of 381".
  const total = source === "all" && lane !== null ? counts?.[lane] : undefined;
  const first = page * pageSize + 1;
  const last = page * pageSize + rows.length;
  const hasNext =
    total !== undefined ? last < total : rows.length === pageSize;

  return (
    <div className="space-y-4">
    <DecisionReviewLanes
      lane={lane ?? "candidates"}
      onLaneChange={changeLane}
      order="review"
      decisions={data}
      {...(counts ? { counts } : {})}
      source={source}
      onSourceChange={changeSource}
      onAccept={(d) => void review(d, "accept")}
      onDismiss={(d) => void review(d, "dismiss")}
      onReaffirm={(d) => void review(d, "reaffirm")}
      onDismissMany={dismissMany}
      pendingIds={pending}
      repoId={repoId}
      LinkComponent={Link}
      error={error}
      isLoading={isLoading || lane === null}
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
            <PageButton
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasNext || isLoading}
            >
              {t("review.next")}
            </PageButton>
          </div>
        </div>
      )}
    </div>
  );
}


/**
 * The review writes and their in-flight set: one record at a time for the
 * row and drawer verbs, a capped loop for a selection.
 */
function useReviewWrites(repoId: string, refresh: () => Promise<unknown>) {
  const t = useTranslations("decisions");
  // A set, not one id: two accepts can be in flight, and a single value let
  // the first to resolve re-enable the second row's buttons while its own
  // write was still running, so a second click double-wrote.
  const [pending, setPending] = React.useState<ReadonlySet<string>>(new Set());
  const review = React.useCallback(
    async (d: DecisionRecord, verb: "accept" | "dismiss" | "reaffirm") => {
      setPending((prev) => new Set(prev).add(d.id));
      try {
        if (verb === "reaffirm") {
          await reaffirmDecision(repoId, d.id);
        } else {
          await patchDecision(repoId, d.id, {
            status: verb === "accept" ? "active" : "dismissed",
          });
        }
        toast.success(
          verb === "accept"
            ? t("review.accepted")
            : verb === "dismiss"
              ? t("review.dismissed")
              : t("review.reaffirmed"),
        );
        // Both, and in parallel: a review action moves a record between lanes,
        // so refreshing the rows without the badges leaves every tab count
        // contradicting the list under it.
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
            message === null
              ? t("review.actionFailed")
              : verb === "accept"
                ? t("review.acceptFailed", { message })
                : verb === "dismiss"
                  ? t("review.dismissFailed", { message })
                  : t("review.reaffirmFailed", { message }),
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

/**
 * The lane the section opens on: candidates while any wait, then accepted
 * decisions whose files moved, and the rules only when neither has rows.
 * Null while the counts it is decided from are still loading.
 */
function openingLane(
  counts: DecisionLaneCounts | undefined,
  countsError: unknown,
): DecisionLane | null {
  if (!counts) return countsError ? "candidates" : null;
  if (counts.candidates > 0) return "candidates";
  if (counts.needs_review > 0) return "needs_review";
  return "active";
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
