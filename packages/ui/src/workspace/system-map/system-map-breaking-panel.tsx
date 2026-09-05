"use client";

/**
 * Breaking-changes rail for the Live System Map. Given a `BreakingChangeReport`,
 * it lists each changed provider contract (breaking first) and the consumers it
 * endangers, showing both code sides — the provider file that changed and the
 * consumer file that calls it. Pure presentation: the host owns the fetch and
 * passes the report in; the at-risk badges ride the map's overlay prop.
 *
 * The rows themselves are `BreakingChangeRow`, shared with the contracts page.
 * The rail passes no href builders, so they stay plain text and every click
 * focuses the map instead.
 */

import { AlertTriangle } from "lucide-react";
import type { BreakingChangeReport } from "@repowise-dev/types";
import {
  BreakingChangeRow,
  breakingChangeKey,
  breakingChangeSummary,
  sortChangesBySeverity,
} from "../breaking-change-row";
import { SystemMapRailPanel } from "./system-map-rail";

export interface SystemMapBreakingPanelProps {
  report: BreakingChangeReport | null;
  loading?: boolean;
  /** Focus a provider/consumer service on the map (optional). */
  onSelectNode?: (nodeId: string) => void;
  onClear: () => void;
}

export function SystemMapBreakingPanel({
  report,
  loading,
  onSelectNode,
  onClear,
}: SystemMapBreakingPanelProps) {
  if (!report) return null;

  const sorted = sortChangesBySeverity(report.changes);

  return (
    <SystemMapRailPanel
      eyebrow="Breaking changes"
      icon={<AlertTriangle size={13} style={{ color: "var(--color-risk-high)" }} />}
      onClear={onClear}
      clearLabel="Clear breaking changes"
      summary={
        loading
          ? "Checking the latest update…"
          : !report.generated_at
            ? "Not yet checked"
            : breakingChangeSummary(report)
      }
    >
      {!loading && report.changes.length === 0 && (
        <div className="p-3 text-[var(--color-text-tertiary)]">
          {/* No timestamp means detection never wrote a result here, so an
              empty change list is silence rather than an all-clear. */}
          {!report.generated_at
            ? "Breaking-change detection has not run for this workspace. Run a workspace update to produce a result."
            : "No breaking changes in the most recent update."}
        </div>
      )}

      {sorted.map((change) => (
        <BreakingChangeRow
          key={breakingChangeKey(change)}
          change={change}
          {...(onSelectNode ? { onSelectNode } : {})}
        />
      ))}
    </SystemMapRailPanel>
  );
}
