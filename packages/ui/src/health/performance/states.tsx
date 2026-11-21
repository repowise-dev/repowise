"use client";

import { AlertTriangle, Gauge, Info } from "lucide-react";
import type { PerformanceOpportunitySummary } from "@repowise-dev/types/health";

import { EmptyState } from "../../shared/empty-state";
import { Skeleton, SkeletonRegion } from "../../ui/skeleton";
import { AiPromptButton } from "../ai-prompt-button";
import { buildConnectEvidencePrompt } from "../ai-prompts/setup-prompt";

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
      tone="error"
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
      icon={<Gauge />}
      title="This index has not been analyzed for performance"
      description={
        summary.detail ??
        summary.reason ??
        "Run an index update to build the causal queue. Until then there is no result to report, which is not the same as a clean one."
      }
    />
  );
}

/**
 * No runtime evidence is stored for this repository. Says what fills the view
 * (profiles and benchmark results) and hands the agent the one prompt that
 * collects them; `found` names files already on disk, when there are any.
 */
export function ConnectEvidenceState({
  found,
  variant = "empty",
}: {
  found?: readonly string[] | null | undefined;
  /**
   * `row` is the slim form above a queue that has causes: the queue keeps its
   * place above the fold, and the row says why nothing in it is measured.
   */
  variant?: "empty" | "row";
}) {
  if (variant === "row") {
    return (
      <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2.5 border-y border-[var(--color-border-default)] py-3">
        <p className="max-w-[70ch] text-sm text-[var(--color-text-secondary)]">
          <strong className="font-semibold text-[var(--color-text-primary)]">
            Ranked by code shape.
          </strong>{" "}
          No profile or benchmark is connected, so nothing here is measured. One prompt has your
          agent run the repo&apos;s own benchmarks and add the results.
        </p>
        <AiPromptButton
          label="Copy prompt for your agent"
          copyText={() => buildConnectEvidencePrompt()}
        />
      </div>
    );
  }
  const shown = found?.slice(0, 3) ?? [];
  const more = (found?.length ?? 0) - shown.length;
  return (
    <EmptyState
      icon={<Gauge />}
      title="Add runtime evidence to rank by measured time"
      description="Repowise reads code shape on its own. Profiles and benchmark results show where time is actually spent, so slow paths are ranked by measurement, not inference."
    >
      <div className="flex flex-col items-center gap-2">
        {shown.length > 0 ? (
          <p className="text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
            Found on disk:{" "}
            {shown.map((path, i) => (
              <span key={path}>
                {i > 0 ? ", " : ""}
                <code className="font-mono">{path}</code>
              </span>
            ))}
            {more > 0 ? ` and ${more} more` : ""}.
          </p>
        ) : null}
        <AiPromptButton
          label="Copy prompt for your agent"
          copyText={() => buildConnectEvidencePrompt()}
        />
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Or run <code className="font-mono">repowise evidence setup</code> yourself.
        </p>
      </div>
    </EmptyState>
  );
}

/**
 * The slim connect row above a queue that has causes, shown only when the
 * host knows no runtime evidence is connected.
 */
export function ConnectEvidenceRow({
  runtimeEvidence,
  hasCauses,
}: {
  runtimeEvidence: { connected: boolean } | null;
  hasCauses: boolean;
}) {
  if (!runtimeEvidence || runtimeEvidence.connected || !hasCauses) return null;
  return <ConnectEvidenceState variant="row" />;
}

/**
 * Analyzed, and nothing in this scope needs a change: the good state. It
 * names the scope and commit it holds for; the accounting sentence beneath
 * says where the rest of the repository is, so "clear" is a statement about
 * something specific and the reader can check the rest.
 */
export function GoodState({
  scope,
  analyzedCommit,
  children,
}: {
  /** "worth doing now in production code", "in all code". */
  scope: string;
  analyzedCommit: string | null | undefined;
  /** The accounting sentence for the scope. */
  children?: React.ReactNode;
}) {
  const at = analyzedCommit ? ` at ${analyzedCommit.slice(0, 7)}` : "";
  return (
    <EmptyState
      tone="positive"
      title={`No performance changes ${scope}${at}`}
      description="The detectors read code shape, not latency, and are high precision and low recall."
    >
      {children}
    </EmptyState>
  );
}

export function FilteredEmpty({ onClear }: { onClear: () => void }) {
  return (
    <EmptyState
      tone="filtered"
      title="No opportunities match these filters"
      description="The counts beside each filter already account for the other active filters. This combination may come from a restored view state that no longer matches the index."
      action={{ label: "Clear filters", onClick: onClear }}
    />
  );
}

/** A one-line notice on a hairline: warning ink for a caveat, neutral otherwise. */
export function Notice({
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
        className="rounded text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        Dismiss
      </button>
    </p>
  );
}
