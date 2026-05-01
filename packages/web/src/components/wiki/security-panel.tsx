"use client";

import useSWR from "swr";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { listSecurityFindings } from "@/lib/api/security";
import type { SecurityFinding } from "@/lib/api/security";

interface SecurityPanelProps {
  repoId: string;
  filePath: string;
}

function severityVariant(severity: string): "outdated" | "stale" | "default" {
  if (severity === "high") return "outdated";
  if (severity === "med") return "stale";
  return "default";
}

function severityLabel(severity: string): string {
  if (severity === "high") return "High";
  if (severity === "med") return "Med";
  return "Low";
}

export function SecurityPanel({ repoId, filePath }: SecurityPanelProps) {
  const { data: findings, isLoading } = useSWR<SecurityFinding[]>(
    ["security", repoId, filePath],
    () => listSecurityFindings(repoId, { file_path: filePath, limit: 20 }),
    { revalidateOnFocus: false },
  );

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
            <Badge variant={severityVariant(f.severity)} className="text-[10px] px-1 py-0 leading-4">
              {severityLabel(f.severity)}
            </Badge>
            <span className="text-xs font-medium text-[var(--color-text-secondary)] truncate">
              {f.kind}
            </span>
          </div>
          {f.snippet && (
            <pre className="text-[10px] font-mono text-[var(--color-text-tertiary)] whitespace-pre-wrap break-all line-clamp-3 leading-relaxed">
              {f.snippet.length > 120 ? f.snippet.slice(0, 120) + "â€¦" : f.snippet}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}
