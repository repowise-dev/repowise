"use client";

import * as React from "react";
import useSWR from "swr";
import Link from "next/link";
import { toast } from "sonner";
import {
  DecisionReviewLanes,
  type DecisionLane,
} from "@repowise-dev/ui/decisions/decision-review-lanes";
import type { DecisionRecord } from "@repowise-dev/types/decisions";
import {
  getDecisionLaneCounts,
  listDecisions,
  patchDecision,
} from "@/lib/api/decisions";

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
  const [lane, setLane] = React.useState<DecisionLane>("active");
  // A set, not one id: two accepts can be in flight, and a single value let
  // the first to resolve re-enable the second row's buttons while its own
  // write was still running, so a second click double-wrote.
  const [pending, setPending] = React.useState<ReadonlySet<string>>(new Set());
  const [page, setPage] = React.useState(0);
  const [source, setSource] = React.useState("all");

  const { data, error, mutate, isLoading } = useSWR(
    [`/api/repos/${repoId}/decisions`, "lane", lane, source, page, pageSize],
    () =>
      listDecisions(repoId, {
        lane,
        ...(source !== "all" ? { source } : {}),
        include_proposed: true,
        limit: pageSize,
        offset: page * pageSize,
      }),
  );

  // One aggregate over the acceptance join, not four page fetches. The badges
  // and the rows they label therefore come from the same question, and a lane
  // holding more rows than a page can carry still reports a measured total.
  //
  // Deliberately not /decisions/counts: that endpoint groups the status
  // column, which is the projection rather than the authority, so its
  // "active" would contradict the lane a reader then opened.
  const { data: counts, mutate: mutateCounts } = useSWR(
    [`/api/repos/${repoId}/decisions/lane-counts`],
    () => getDecisionLaneCounts(repoId),
  );

  // A lane change invalidates the offset: page 3 of Candidates is not page 3
  // of Active, and leaving it put lands the reader on an empty lane.
  // Either control invalidates the offset: page 3 of Candidates is not page 3
  // of Active, and leaving it put lands the reader on an empty lane.
  const changeLane = React.useCallback((next: DecisionLane) => {
    setLane(next);
    setPage(0);
  }, []);

  const changeSource = React.useCallback((next: string) => {
    setSource(next);
    setPage(0);
  }, []);

  const review = React.useCallback(
    async (d: DecisionRecord, status: "active" | "dismissed") => {
      setPending((prev) => new Set(prev).add(d.id));
      try {
        await patchDecision(repoId, d.id, { status });
        toast.success(
          status === "active"
            ? "Accepted. It governs the files it names."
            : "Dismissed. Reindexing will not propose it again.",
        );
        // Both, and in parallel: a review action moves a record between lanes,
        // so refreshing the rows without the badges leaves every tab count
        // contradicting the list under it.
        await Promise.all([mutate(), mutateCounts()]);
      } catch (err) {
        // The acceptance contract refuses a record with no reason, scope or
        // evidence, and it names which. Surface that sentence rather than a
        // generic failure: it is the whole instruction for what to do next.
        toast.error(
          err instanceof Error
            ? `Couldn't ${status === "active" ? "accept" : "dismiss"}: ${err.message}`
            : "The review action failed.",
        );
      } finally {
        setPending((prev) => {
          const next = new Set(prev);
          next.delete(d.id);
          return next;
        });
      }
    },
    [repoId, mutate, mutateCounts],
  );

  const rows = data ?? [];
  // Only when the rows and the badge count the same set. Under a source filter
  // the lane count is the wrong denominator, so the range says what it loaded
  // and nothing more rather than "1-12 of 381".
  const total = source === "all" ? counts?.[lane] : undefined;
  const first = page * pageSize + 1;
  const last = page * pageSize + rows.length;
  const hasNext =
    total !== undefined ? last < total : rows.length === pageSize;

  return (
    <div className="space-y-4">
    <DecisionReviewLanes
      lane={lane}
      onLaneChange={changeLane}
      decisions={data}
      {...(counts ? { counts } : {})}
      source={source}
      onSourceChange={changeSource}
      onAccept={(d) => void review(d, "active")}
      onDismiss={(d) => void review(d, "dismissed")}
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
            {total !== undefined ? ` of ${total.toLocaleString()}` : ""}
          </p>
          <div className="flex items-center gap-2">
            <PageButton
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0 || isLoading}
            >
              Previous
            </PageButton>
            <PageButton
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasNext || isLoading}
            >
              Next
            </PageButton>
          </div>
        </div>
      )}
    </div>
  );
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
