"use client";

/**
 * Governance rail for the Live System Map. Given a `ConformanceReport`, it lists
 * the dependency-rule violations (the explicit policy breaches) and the
 * dependency cycles (the structural smells), each linking to the services
 * involved. Pure presentation: the host owns the fetch and passes the report in;
 * the violation/cycle badges ride the map's overlay prop.
 */

import { ShieldAlert, RefreshCw, X } from "lucide-react";
import type {
  ConformanceReport,
  ConformanceViolation,
  DependencyCycle,
} from "@repowise-dev/types";

export interface SystemMapConformancePanelProps {
  report: ConformanceReport | null;
  loading?: boolean;
  /** Focus a service on the map (optional). */
  onSelectNode?: (nodeId: string) => void;
  onClear: () => void;
}

const panelStyle: React.CSSProperties = {
  position: "absolute",
  top: 12,
  right: 12,
  width: 340,
  maxHeight: "calc(100% - 24px)",
  overflowY: "auto",
  background: "var(--color-bg-elevated)",
  border: "1px solid var(--color-border-default)",
  borderRadius: 10,
  boxShadow: "0 8px 24px rgba(0,0,0,0.28)",
  zIndex: 4,
  fontSize: 12,
  color: "var(--color-text-secondary)",
};

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
      title={id}
      style={{
        cursor: onSelectNode ? "pointer" : "default",
        background: "transparent",
        color: "var(--color-text-primary)",
        fontWeight: 600,
        padding: 0,
      }}
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
    <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--color-border-subtle)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <span
          style={{
            flexShrink: 0,
            color,
            border: `1px solid color-mix(in srgb, ${color} 45%, transparent)`,
            background: `color-mix(in srgb, ${color} 16%, transparent)`,
            borderRadius: 4,
            fontSize: 9,
            fontWeight: 700,
            textTransform: "uppercase",
            padding: "1px 5px",
          }}
        >
          violation
        </span>
        <NodeButton
          id={violation.source}
          label={violation.source_name || violation.source}
          {...(onSelectNode ? { onSelectNode } : {})}
        />
        <span style={{ color: "var(--color-text-tertiary)" }}>→</span>
        <NodeButton
          id={violation.target}
          label={violation.target_name || violation.target}
          {...(onSelectNode ? { onSelectNode } : {})}
        />
        <span style={{ color: "var(--color-text-tertiary)", fontSize: 10 }}>
          ({violation.edge_kind})
        </span>
      </div>
      <div style={{ color: "var(--color-text-secondary)", marginTop: 3 }}>
        breaks rule{" "}
        <code style={{ color: "var(--color-warning)" }}>
          {violation.rule_source} !-&gt; {violation.rule_target}
        </code>
      </div>
      {violation.rule_description && (
        <div style={{ color: "var(--color-text-tertiary)", fontSize: 10, marginTop: 2 }}>
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
    <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--color-border-subtle)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span
          style={{
            flexShrink: 0,
            color,
            border: `1px solid color-mix(in srgb, ${color} 45%, transparent)`,
            background: `color-mix(in srgb, ${color} 16%, transparent)`,
            borderRadius: 4,
            fontSize: 9,
            fontWeight: 700,
            textTransform: "uppercase",
            padding: "1px 5px",
          }}
        >
          cycle · {cycle.length}
        </span>
      </div>
      <div style={{ marginTop: 4, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4 }}>
        {cycle.nodes.map((nid, i) => (
          <span key={nid} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <NodeButton id={nid} label={nid} {...(onSelectNode ? { onSelectNode } : {})} />
            {i < cycle.nodes.length - 1 && (
              <span style={{ color: "var(--color-text-tertiary)" }}>→</span>
            )}
          </span>
        ))}
        <span style={{ color: "var(--color-text-tertiary)" }}>↩</span>
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

  return (
    <div style={panelStyle}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 12px",
          borderBottom: "1px solid var(--color-border-default)",
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <ShieldAlert size={13} style={{ color: "var(--color-risk-high)" }} />
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: 0.6,
              textTransform: "uppercase",
              color: "var(--color-text-tertiary)",
            }}
          >
            Architecture conformance
          </span>
        </span>
        <button
          type="button"
          onClick={onClear}
          aria-label="Clear conformance"
          style={{ cursor: "pointer", color: "var(--color-text-tertiary)", display: "inline-flex" }}
        >
          <X size={14} />
        </button>
      </div>

      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--color-border-default)" }}>
        <div style={{ color: "var(--color-text-tertiary)", fontSize: 11 }}>
          {loading
            ? "Checking the latest update…"
            : `${report.violation_count} violation(s), ${report.cycle_count} cycle(s) from ${report.rules_evaluated} rule(s)`}
        </div>
      </div>

      {!loading && report.violations.length === 0 && report.cycles.length === 0 && (
        <div
          style={{
            padding: "12px",
            color: "var(--color-text-tertiary)",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <RefreshCw size={12} />
          {report.rules_evaluated > 0
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
    </div>
  );
}
