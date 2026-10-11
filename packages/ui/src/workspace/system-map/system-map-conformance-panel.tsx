"use client";

/**
 * Governance rail for the Live System Map. Given a `ConformanceReport`, it lists
 * the dependency-rule violations (the explicit policy breaches) and the
 * dependency cycles (the structural smells), each linking to the services
 * involved. Pure presentation: the host owns the fetch and passes the report in;
 * the violation/cycle badges ride the map's overlay prop.
 */

import { ShieldAlert, RefreshCw } from "lucide-react";
import type {
  ConformanceReport,
  ConformanceViolation,
  DependencyCycle,
} from "@repowise-dev/types";
import { RailChip, SystemMapRailPanel } from "./system-map-rail";

export interface SystemMapConformancePanelProps {
  report: ConformanceReport | null;
  loading?: boolean;
  /** Focus a service on the map (optional). */
  onSelectNode?: (nodeId: string) => void;
  onClear: () => void;
}

function NodeButton({
  id,
  label,
  onSelectNode,
}: {
  id: string;
  label: string;
  onSelectNode?: (id: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelectNode?.(id)}
      disabled={!onSelectNode}
      title={id}
      className={`p-0 font-semibold text-[var(--color-text-primary)] ${
        onSelectNode ? "cursor-pointer hover:underline" : "cursor-default"
      }`}
    >
      {label}
    </button>
  );
}

function ViolationRow({
  violation,
  onSelectNode,
}: {
  violation: ConformanceViolation;
  onSelectNode?: (id: string) => void;
}) {
  const color = "var(--color-risk-high)";
  return (
    <div className="border-b border-[var(--color-border-subtle)] px-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <RailChip color={color}>violation</RailChip>
        <NodeButton
          id={violation.source}
          label={violation.source_name || violation.source}
          {...(onSelectNode ? { onSelectNode } : {})}
        />
        <span className="text-[var(--color-text-tertiary)]">→</span>
        <NodeButton
          id={violation.target}
          label={violation.target_name || violation.target}
          {...(onSelectNode ? { onSelectNode } : {})}
        />
        <span className="text-[10px] text-[var(--color-text-tertiary)]">({violation.edge_kind})</span>
      </div>
      <div className="mt-[3px] text-[var(--color-text-secondary)]">
        breaks rule{" "}
        <code className="text-[var(--color-warning)]">
          {violation.rule_source} !-&gt; {violation.rule_target}
        </code>
      </div>
      {violation.rule_description && (
        <div className="mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">
          {violation.rule_description}
        </div>
      )}
    </div>
  );
}

function CycleRow({
  cycle,
  onSelectNode,
}: {
  cycle: DependencyCycle;
  onSelectNode?: (id: string) => void;
}) {
  const color = "var(--color-warning)";
  return (
    <div className="border-b border-[var(--color-border-subtle)] px-3 py-2">
      <div className="flex items-center gap-1.5">
        <RailChip color={color}>cycle · {cycle.length}</RailChip>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        {cycle.nodes.map((nid, i) => (
          <span key={nid} className="inline-flex items-center gap-1">
            <NodeButton id={nid} label={nid} {...(onSelectNode ? { onSelectNode } : {})} />
            {i < cycle.nodes.length - 1 && <span className="text-[var(--color-text-tertiary)]">→</span>}
          </span>
        ))}
        <span className="text-[var(--color-text-tertiary)]">↩</span>
      </div>
    </div>
  );
}

export function SystemMapConformancePanel({
  report,
  loading,
  onSelectNode,
  onClear,
}: SystemMapConformancePanelProps) {
  if (!report) return null;

  // The report lists at most MAX_CYCLES; total_cycles is how many exist. Say
  // so when they differ rather than passing the cap off as the count.
  const totalCycles = report.total_cycles ?? report.cycle_count;
  const cycleSummary =
    totalCycles > report.cycle_count
      ? `${report.cycle_count} of ${totalCycles} cycles`
      : `${report.cycle_count} ${report.cycle_count === 1 ? "cycle" : "cycles"}`;
  const violations = `${report.violation_count} ${
    report.violation_count === 1 ? "violation" : "violations"
  }`;
  const rules = `${report.rules_evaluated} ${report.rules_evaluated === 1 ? "rule" : "rules"}`;

  return (
    <SystemMapRailPanel
      eyebrow="Architecture conformance"
      icon={<ShieldAlert size={13} style={{ color: "var(--color-risk-high)" }} />}
      onClear={onClear}
      clearLabel="Clear conformance"
      summary={
        loading
          ? "Checking the latest update…"
          : !report.generated_at
            ? "Not yet checked"
            : `${violations}, ${cycleSummary} from ${rules}`
      }
    >
      {!loading && report.violations.length === 0 && report.cycles.length === 0 && (
        <div className="flex items-center gap-1.5 p-3 text-[var(--color-text-tertiary)]">
          <RefreshCw size={12} />
          {/* An unstamped report is one the checker never wrote a result into.
              Reporting its zeros as "no violations" is the failure this panel
              exists to avoid. */}
          {!report.generated_at
            ? "This workspace has not been checked. Run a workspace update to produce a result."
            : report.rules_evaluated > 0
              ? "No rule violations or dependency cycles."
              : "No dependency cycles. Declare conformance rules to enforce allowed dependencies."}
        </div>
      )}

      {report.violations.map((v) => (
        <ViolationRow
          key={`${v.edge_id}:${v.rule_source}:${v.rule_target}`}
          violation={v}
          {...(onSelectNode ? { onSelectNode } : {})}
        />
      ))}
      {report.cycles.map((c) => (
        <CycleRow key={c.nodes.join("->")} cycle={c} {...(onSelectNode ? { onSelectNode } : {})} />
      ))}
    </SystemMapRailPanel>
  );
}
