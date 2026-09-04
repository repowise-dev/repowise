"use client";

import { AlertTriangle, Gauge, Info } from "lucide-react";
import type { PerformanceOpportunitySummary } from "@repowise-dev/types/health";

import { EmptyState } from "../../shared/empty-state";
import { Skeleton, SkeletonRegion } from "../../ui/skeleton";

/**
 * Every state the queue can be in other than "here are the rows". They are
 * separated because each says something different: an index that was never
 * analyzed, a model that moved, a filter that matched nothing, and a
 * repository with no supported pattern are four answers, and none of them is
 * "this code is fast".
 */

export function QueueSkeleton() {
  return (
    <SkeletonRegion className="space-y-8" label="Loading performance opportunities">
      <Skeleton className="h-32 rounded" />
      <Skeleton className="h-10 rounded" />
      <div className="space-y-px">
        {[0, 1, 2, 3, 4].map((row) => (
          <Skeleton key={row} className="h-[86px] rounded-none" />
        ))}
      </div>
    </SkeletonRegion>
  );
}

export function QueueError({ onRetry }: { onRetry?: (() => void) | undefined }) {
  return (
    <EmptyState
      icon={<Gauge className="h-6 w-6" />}
      title="Could not load performance opportunities"
      description="The repository may need indexing, or the server may be temporarily unavailable. Nothing here says the code is fast."
      {...(onRetry ? { action: { label: "Try again", onClick: onRetry } } : {})}
    />
  );
}

/** The index carries no analysis yet. Distinct from a repository with none. */
export function UnavailableQueue({ summary }: { summary: PerformanceOpportunitySummary }) {
  return (
    <EmptyState
      icon={<Gauge className="h-6 w-6" />}
      title="This index has not been analyzed for performance"
      description={
        summary.detail ??
        summary.reason ??
        "Run an index update to build the causal queue. Until then there is no result to report, which is not the same as a clean one."
      }
    />
  );
}

/** Analyzed, and nothing supported surfaced. Say exactly that. */
export function EmptyQueue() {
  return (
    <div className="border-t border-[var(--color-border-default)] px-1 py-12 text-center">
      <h3 className="text-[15px] font-semibold text-[var(--color-text-primary)]">
        No supported pattern surfaced
      </h3>
      <p className="mx-auto mt-2 max-w-[60ch] text-sm text-[var(--color-text-tertiary)]">
        The detectors are high precision and low recall, so an empty queue means nothing they
        recognize was found. It does not measure latency and does not claim the code is fast.
      </p>
    </div>
  );
}

export function FilteredEmpty({ onClear }: { onClear: () => void }) {
  return (
    <div className="border-t border-[var(--color-border-default)] px-1 py-12 text-center">
      <h3 className="text-[15px] font-semibold text-[var(--color-text-primary)]">
        No opportunities match these filters
      </h3>
      <p className="mx-auto mt-2 max-w-[60ch] text-sm text-[var(--color-text-tertiary)]">
        The counts beside each filter already account for the other active filters. This
        combination may come from a restored view state that no longer matches the index.
      </p>
      <button
        type="button"
        onClick={onClear}
        className="mt-4 rounded text-sm font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        Clear filters
      </button>
    </div>
  );
}

function Notice({
  tone,
  children,
}: {
  tone: "warning" | "neutral";
  children: React.ReactNode;
}) {
  const Icon = tone === "warning" ? AlertTriangle : Info;
  const ink = tone === "warning" ? "var(--color-warning)" : "var(--color-text-tertiary)";
  return (
    <p
      role="status"
      className="flex items-start gap-2 border-l-2 py-1.5 pl-3 text-sm text-[var(--color-text-secondary)]"
      style={{ borderColor: ink }}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" style={{ color: ink }} aria-hidden="true" />
      <span className="min-w-0">{children}</span>
    </p>
  );
}

/** An id minted by an older model still resolves; it just needs a refresh. */
export function StaleModelNotice({ summary }: { summary: PerformanceOpportunitySummary }) {
  return (
    <Notice tone="warning">
      These opportunities were grouped by an earlier analysis model
      {summary.performance_model_version != null
        ? ` (version ${summary.performance_model_version})`
        : ""}
      . Ids and grouping can move on the next index update, so treat a saved link as approximate.
    </Notice>
  );
}

/** Name the value the server did not recognize instead of showing zero rows. */
export function IgnoredArgumentsNotice({ ignored }: { ignored: Record<string, string> }) {
  const entries = Object.entries(ignored);
  if (entries.length === 0) return null;
  return (
    <Notice tone="warning">
      The server did not recognize{" "}
      {entries.map(([key, value], index) => (
        <span key={key}>
          {index > 0 ? ", " : ""}
          <code className="font-mono text-xs">
            {key}={value}
          </code>
        </span>
      ))}
      , so that filter was not applied. The counts below describe the unfiltered result.
    </Notice>
  );
}

/**
 * A link named a cause this index cannot resolve.
 *
 * Says which id, because the link is shareable and the reader may be holding
 * it somewhere else, and offers the queue rather than leaving the page looking
 * like it simply ignored them.
 */
export function LinkedCauseUnavailable({
  opportunityId,
  detail,
  onDismiss,
}: {
  opportunityId: string;
  detail: string | null;
  onDismiss: () => void;
}) {
  return (
    <p
      role="status"
      className="flex flex-wrap items-baseline gap-x-2 border-l-2 border-[var(--color-warning)] py-1.5 pl-3 text-sm text-[var(--color-text-secondary)]"
    >
      <span>
        The link named <span className="break-all font-mono">{opportunityId}</span>, which this
        index does not hold.{detail ? ` ${detail}` : ""} The queue below is unfiltered.
      </span>
      <button
        type="button"
        onClick={onDismiss}
        className="rounded text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        Dismiss
      </button>
    </p>
  );
}
