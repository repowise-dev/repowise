"use client";

/**
 * Breaking-changes rail for the Live System Map. Given a `BreakingChangeReport`,
 * it lists each changed provider contract (breaking first) and the consumers it
 * endangers, showing both code sides — the provider file that changed and the
 * consumer file that calls it. Pure presentation: the host owns the fetch and
 * passes the report in; the at-risk badges ride the map's overlay prop.
 */

import { AlertTriangle, X } from "lucide-react";
import type { BreakingChange, BreakingChangeReport } from "@repowise-dev/types";

export interface SystemMapBreakingPanelProps {
  report: BreakingChangeReport | null;
  loading?: boolean;
  /** Focus a provider/consumer service on the map (optional). */
  onSelectNode?: (nodeId: string) => void;
  onClear: () => void;
}

const panelStyle: React.CSSProperties = {
  position: "absolute",
  top: 12,
  right: 12,
  width: 320,
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
          {change.severity}
        </span>
        <button
          type="button"
          onClick={() => onSelectNode?.(change.provider_node_id)}
          title={`Provider: ${change.provider_repo} · ${change.provider_file}`}
          style={{
            flex: 1,
            minWidth: 0,
            textAlign: "left",
            cursor: onSelectNode ? "pointer" : "default",
            background: "transparent",
            color: "var(--color-text-primary)",
            fontWeight: 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {change.contract_id}
        </button>
      </div>
      <div style={{ color: "var(--color-text-secondary)", marginTop: 3 }}>{change.detail}</div>
      <div style={{ color: "var(--color-text-tertiary)", fontSize: 10, marginTop: 2 }}>
        {change.provider_repo} · {change.provider_file}
      </div>
      {change.impacted_consumers.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 10, fontWeight: 700, color: "var(--color-text-tertiary)" }}>
            Endangers {change.impacted_consumers.length} consumer(s)
          </div>
          {change.impacted_consumers.map((c) => (
            <button
              key={`${c.node_id}:${c.file}`}
              type="button"
              onClick={() => onSelectNode?.(c.node_id)}
              title={`Consumer: ${c.repo} · ${c.file}`}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                cursor: onSelectNode ? "pointer" : "default",
                background: "transparent",
                color: "var(--color-text-secondary)",
                fontSize: 11,
                padding: "2px 0 2px 8px",
              }}
            >
              <span style={{ color: "var(--color-text-primary)" }}>{c.repo}</span> · {c.file}
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
          <AlertTriangle size={13} style={{ color: "var(--color-risk-high)" }} />
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: 0.6,
              textTransform: "uppercase",
              color: "var(--color-text-tertiary)",
            }}
          >
            Breaking changes
          </span>
        </span>
        <button
          type="button"
          onClick={onClear}
          aria-label="Clear breaking changes"
          style={{ cursor: "pointer", color: "var(--color-text-tertiary)", display: "inline-flex" }}
        >
          <X size={14} />
        </button>
      </div>

      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--color-border-default)" }}>
        <div style={{ color: "var(--color-text-tertiary)", fontSize: 11 }}>
          {loading
            ? "Checking the latest update…"
            : `${report.breaking_count} breaking, ${report.warning_count} warning across ${report.impacted_repos.length} repo(s)`}
        </div>
      </div>

      {!loading && report.changes.length === 0 && (
        <div style={{ padding: "12px", color: "var(--color-text-tertiary)" }}>
          No breaking changes in the most recent update.
        </div>
      )}

      {sorted.map((change) => (
        <ChangeRow
          key={`${change.contract_id}:${change.kind}:${change.field_name ?? ""}`}
          change={change}
          {...(onSelectNode ? { onSelectNode } : {})}
        />
      ))}
    </div>
  );
}
