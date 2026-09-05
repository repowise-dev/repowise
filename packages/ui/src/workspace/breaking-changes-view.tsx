"use client";

/**
 * Breaking changes as a page section rather than a map rail: the provider
 * contracts that changed in the last workspace update and the consumers they
 * endanger, breaking first.
 *
 * The empty states are two different facts and are worded as two: a report that
 * never ran is silence, and only a report with a timestamp can say "nothing
 * broke". Reading the first as the second is the failure mode this view exists
 * to avoid.
 */

import { AlertTriangle } from "lucide-react";
import type { BreakingChange, BreakingChangeReport } from "@repowise-dev/types";
import { Card } from "../ui/card";
import { EmptyState } from "../shared/empty-state";
import {
  BreakingChangeRow,
  breakingChangeKey,
  breakingChangeSummary,
  sortChangesBySeverity,
  type BreakingChangeLinks,
} from "./breaking-change-row";

export interface BreakingChangesViewProps {
  /** The latest report, or null while it is loading or unavailable. */
  report: BreakingChangeReport | null;
  loading?: boolean;
  links?: BreakingChangeLinks;
  onSelectContract?: (contractId: string, change: BreakingChange) => void;
  /** Focus a service on a map, when the host has one. */
  onSelectNode?: (nodeId: string) => void;
}

const NOT_RUN_TITLE = "Breaking-change detection has not run";
const NOT_RUN_BODY =
  "Detection compares each update against the previously indexed contracts. Run a workspace update to produce a first result.";

export function BreakingChangesView({
  report,
  loading,
  links,
  onSelectContract,
  onSelectNode,
}: BreakingChangesViewProps) {
  if (loading) {
    return (
      <Card className="px-3 py-4 text-xs text-[var(--color-text-tertiary)]">
        Checking the latest update…
      </Card>
    );
  }

  // No report at all and a report with no timestamp are the same fact: nothing
  // has been compared yet, so an empty change list is not an all-clear.
  if (!report || !report.generated_at) {
    return <EmptyState className="p-6" title={NOT_RUN_TITLE} description={NOT_RUN_BODY} />;
  }

  if (report.changes.length === 0) {
    return (
      <EmptyState
        className="p-6"
        title="No breaking changes in the most recent update"
        description="Every provider contract that changed still matches the consumers linked to it."
      />
    );
  }

  const sorted = sortChangesBySeverity(report.changes);

  return (
    <Card className="overflow-hidden text-xs text-[var(--color-text-secondary)]">
      <div className="flex items-center gap-1.5 border-b border-[var(--color-border-default)] px-3 py-2 text-[11px] text-[var(--color-text-tertiary)]">
        <AlertTriangle size={13} style={{ color: "var(--color-risk-high)" }} />
        <span>{breakingChangeSummary(report)}</span>
      </div>
      {sorted.map((change) => (
        <BreakingChangeRow
          key={breakingChangeKey(change)}
          change={change}
          {...(links ? { links } : {})}
          {...(onSelectContract ? { onSelectContract } : {})}
          {...(onSelectNode ? { onSelectNode } : {})}
        />
      ))}
    </Card>
  );
}
