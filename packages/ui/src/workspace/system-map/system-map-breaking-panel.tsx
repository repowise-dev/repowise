"use client";

/**
 * Breaking-changes rail for the Live System Map. Given a `BreakingChangeReport`,
 * it lists each changed provider contract (breaking first) and the consumers it
 * endangers, showing both code sides — the provider file that changed and the
 * consumer file that calls it. Pure presentation: the host owns the fetch and
 * passes the report in; the at-risk badges ride the map's overlay prop.
 */

import { AlertTriangle } from "lucide-react";
import type { BreakingChange, BreakingChangeReport } from "@repowise-dev/types";
import { RailChip, SystemMapRailPanel } from "./system-map-rail";

export interface SystemMapBreakingPanelProps {
  report: BreakingChangeReport | null;
  loading?: boolean;
  /** Focus a provider/consumer service on the map (optional). */
  onSelectNode?: (nodeId: string) => void;
  onClear: () => void;
}

function severityColor(severity: string): string {
  return severity === "breaking" ? "var(--color-risk-high)" : "var(--color-warning)";
}

function ChangeRow({
  change,
  onSelectNode,
}: {
  change: BreakingChange;
  onSelectNode?: (id: string) => void;
}) {
  const color = severityColor(change.severity);
  return (
    <div className="border-b border-[var(--color-border-subtle)] px-3 py-2">
      <div className="flex items-center gap-1.5">
        <RailChip color={color}>{change.severity}</RailChip>
        <button
          type="button"
          onClick={() => onSelectNode?.(change.provider_node_id)}
          disabled={!onSelectNode}
          title={`Provider: ${change.provider_repo} · ${change.provider_file}`}
          className={`min-w-0 flex-1 truncate text-left font-semibold text-[var(--color-text-primary)] ${
            onSelectNode ? "cursor-pointer hover:underline" : "cursor-default"
          }`}
        >
          {change.contract_id}
        </button>
      </div>
      <div className="mt-[3px] text-[var(--color-text-secondary)]">{change.detail}</div>
      <div className="mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">
        {change.provider_repo} · {change.provider_file}
      </div>
      {change.impacted_consumers.length > 0 && (
        <div className="mt-1.5">
          <div className="text-[10px] font-bold text-[var(--color-text-tertiary)]">
            {change.impacted_consumers.length === 1
              ? "Endangers 1 consumer"
              : `Endangers ${change.impacted_consumers.length} consumers`}
          </div>
          {change.impacted_consumers.map((c) => (
            <button
              key={`${c.node_id}:${c.file}`}
              type="button"
              onClick={() => onSelectNode?.(c.node_id)}
              disabled={!onSelectNode}
              title={`Consumer: ${c.repo} · ${c.file}`}
              className={`block w-full truncate py-0.5 pl-2 text-left text-[11px] text-[var(--color-text-secondary)] ${
                onSelectNode ? "cursor-pointer hover:underline" : "cursor-default"
              }`}
            >
              <span className="text-[var(--color-text-primary)]">{c.repo}</span> · {c.file}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function SystemMapBreakingPanel({
  report,
  loading,
  onSelectNode,
  onClear,
}: SystemMapBreakingPanelProps) {
  if (!report) return null;

  const sorted = [...report.changes].sort((a, b) =>
    a.severity === b.severity ? 0 : a.severity === "breaking" ? -1 : 1,
  );

  const repos = report.impacted_repos.length;
  const summary = `${report.breaking_count} breaking, ${report.warning_count} ${
    report.warning_count === 1 ? "warning" : "warnings"
  } across ${repos} ${repos === 1 ? "repo" : "repos"}`;

  return (
    <SystemMapRailPanel
      eyebrow="Breaking changes"
      icon={<AlertTriangle size={13} style={{ color: "var(--color-risk-high)" }} />}
      onClear={onClear}
      clearLabel="Clear breaking changes"
      summary={loading ? "Checking the latest update…" : !report.generated_at ? "Not yet checked" : summary}
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
        <ChangeRow
          key={`${change.contract_id}:${change.kind}:${change.field_name ?? ""}`}
          change={change}
          {...(onSelectNode ? { onSelectNode } : {})}
        />
      ))}
    </SystemMapRailPanel>
  );
}
