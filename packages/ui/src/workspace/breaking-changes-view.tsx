"use client";

/**
 * Breaking changes as a page section rather than a map rail: the provider
 * contracts that changed in the last workspace update and their endpoint-exposed
 * consumers, breaking first.
 *
 * The empty states are two different facts and are worded as two: a report that
 * never ran is silence, and only a report with a timestamp can say "nothing
 * broke". Reading the first as the second is the failure mode this view exists
 * to avoid.
 */

import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import type { BreakingChange, BreakingChangeReport } from "@repowise-dev/types";
import { Card } from "../ui/card";
import { formatRelativeTimeOrNull } from "../lib/format";
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

export function BreakingChangesView({
  report,
  loading,
  links,
  onSelectContract,
  onSelectNode,
}: BreakingChangesViewProps) {
  // Every state without findings is one quiet sentence. A tinted box around
  // "nothing found" reads as the loudest thing on the page, and healthy is the
  // state that should be quiet.
  if (loading) {
    return <Quiet>Checking the latest update...</Quiet>;
  }

  // No report at all and a report with no timestamp are the same fact: nothing
  // has been compared yet, so an empty change list is not an all-clear.
  if (!report || !report.generated_at) {
    return (
      <Quiet>
        Breaking-change detection has not run. It compares each update against the previously
        indexed contracts, so the first result appears after the next workspace update.
      </Quiet>
    );
  }

  if (report.changes.length === 0) {
    const when = formatRelativeTimeOrNull(report.generated_at, "");
    return (
      <Quiet>
        No contract compatibility findings in the most recent update
        {when ? ` (${when})` : ""}: no provider changed in a way that breaks, or might break, a
        linked consumer.
      </Quiet>
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

function Quiet({ children }: { children: ReactNode }) {
  return (
    <p className="max-w-[68ch] text-xs leading-relaxed text-[var(--color-text-secondary)]">
      {children}
    </p>
  );
}
