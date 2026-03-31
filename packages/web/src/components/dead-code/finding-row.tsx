"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { truncatePath, formatConfidence } from "@/lib/utils/format";
import { patchDeadCodeFinding } from "@/lib/api/dead-code";
import { cn } from "@/lib/utils/cn";
import type { DeadCodeFindingResponse } from "@/lib/api/types";

interface FindingRowProps {
  finding: DeadCodeFindingResponse;
  selected: boolean;
  onToggle: (id: string) => void;
  onUpdate: (updated: DeadCodeFindingResponse) => void;
}

const STATUS_COLORS: Record<string, string> = {
  open: "bg-red-500/10 text-red-500 border-red-500/20",
  acknowledged: "bg-yellow-500/10 text-yellow-500 border-yellow-500/20",
  resolved: "bg-green-500/10 text-green-500 border-green-500/20",
  false_positive: "bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] border-[var(--color-border-default)]",
};

export function FindingRow({ finding, selected, onToggle, onUpdate }: FindingRowProps) {
  const [pending, setPending] = useState(false);

  const patch = async (status: string) => {
    setPending(true);
    try {
      const updated = await patchDeadCodeFinding(finding.id, { status });
      onUpdate(updated);
    } finally {
      setPending(false);
    }
  };

  return (
    <tr
      className={cn(
        "border-b border-[var(--color-border-default)] transition-colors last:border-0",
        selected ? "bg-[var(--color-accent-muted)]" : "hover:bg-[var(--color-bg-elevated)]",
      )}
    >
      <td className="px-4 py-2.5">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggle(finding.id)}
          className="rounded border-[var(--color-border-default)]"
        />
      </td>
      <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-primary)]" style={{ maxWidth: 0 }}>
        <span className="truncate block" title={finding.file_path}>{finding.file_path}</span>
        {finding.symbol_name && (
          <span className="text-[var(--color-text-tertiary)]">{finding.symbol_name}</span>
        )}
      </td>
      <td className="px-4 py-2.5 text-xs text-[var(--color-text-secondary)] tabular-nums">
        <span
          className={cn(
            "font-medium",
            finding.confidence >= 0.8
              ? "text-red-500"
              : finding.confidence >= 0.6
                ? "text-yellow-500"
                : "text-[var(--color-text-secondary)]",
          )}
        >
          {formatConfidence(finding.confidence)}
        </span>
      </td>
      <td className="px-4 py-2.5 text-xs text-[var(--color-text-secondary)]">
        {finding.primary_owner ?? "—"}
      </td>
      <td className="px-4 py-2.5 text-xs text-[var(--color-text-tertiary)] tabular-nums">
        {finding.lines}
      </td>
      <td className="px-4 py-2.5">
        {finding.safe_to_delete ? (
          <Badge variant="fresh">Safe</Badge>
        ) : (
          <Badge variant="default">Review</Badge>
        )}
      </td>
      <td className="px-4 py-2.5">
        <span
          className={cn(
            "inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium",
            STATUS_COLORS[finding.status] ?? STATUS_COLORS.open,
          )}
        >
          {finding.status.replace(/_/g, " ")}
        </span>
      </td>
      <td className="px-4 py-2.5">
        {finding.status === "open" && (
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => patch("resolved")}
              className="h-6 px-2 text-xs text-green-500 hover:text-green-400"
            >
              Resolve
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => patch("acknowledged")}
              className="h-6 px-2 text-xs"
            >
              Ack
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => patch("false_positive")}
              className="h-6 px-2 text-xs text-[var(--color-text-tertiary)]"
            >
              FP
            </Button>
          </div>
        )}
      </td>
    </tr>
  );
}
