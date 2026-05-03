"use client";

import type { SecurityFinding, SecuritySeverity } from "@repowise-dev/types";
import { Badge } from "../ui/badge";

export interface SecurityPanelProps {
  /** Findings for the file. Empty / undefined renders an empty-state message. */
  findings: SecurityFinding[] | undefined;
  /** While loading the shell renders nothing. */
  isLoading?: boolean;
}

function severityVariant(severity: SecuritySeverity): "outdated" | "stale" | "default" {
  if (severity === "high") return "outdated";
  if (severity === "med") return "stale";
  return "default";
}

function severityLabel(severity: SecuritySeverity): string {
  if (severity === "high") return "High";
  if (severity === "med") return "Med";
  return "Low";
}

export function SecurityPanel({ findings, isLoading }: SecurityPanelProps) {
  if (isLoading) return null;
  if (!findings || findings.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">No security signals.</p>
    );
  }

  return (
    <div className="space-y-2">
      {findings.map((f) => (
        <div
          key={f.id}
          className="rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-2 space-y-1"
        >
          <div className="flex items-center gap-1.5 flex-wrap">
            <Badge
              variant={severityVariant(f.severity)}
              className="text-[10px] px-1 py-0 leading-4"
            >
              {severityLabel(f.severity)}
            </Badge>
            <span className="text-xs font-medium text-[var(--color-text-secondary)] truncate">
              {f.kind}
            </span>
          </div>
          {f.snippet && (
            <pre className="text-[10px] font-mono text-[var(--color-text-tertiary)] whitespace-pre-wrap break-all line-clamp-3 leading-relaxed">
              {f.snippet.length > 120 ? f.snippet.slice(0, 120) + "…" : f.snippet}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}
