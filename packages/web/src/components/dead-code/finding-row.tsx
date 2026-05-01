"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@repowise/ui/ui/button";
import { Badge } from "@repowise/ui/ui/badge";
import { ConfirmDialog } from "@repowise/ui/ui/confirm-dialog";
import { formatConfidence } from "@repowise/ui/lib/format";
import { patchDeadCodeFinding } from "@/lib/api/dead-code";
import { cn } from "@/lib/utils/cn";
import type { DeadCodeFindingResponse } from "@/lib/api/types";

const STATUS_LABELS: Record<string, { title: string; description: string; confirmLabel: string; destructive: boolean }> = {
  resolved: {
    title: "Resolve finding?",
    description: "Mark this finding as resolved. You can undo from the toast.",
    confirmLabel: "Resolve",
    destructive: false,
  },
  acknowledged: {
    title: "Acknowledge finding?",
    description: "Mark this finding as acknowledged.",
    confirmLabel: "Acknowledge",
    destructive: false,
  },
  false_positive: {
    title: "Mark as false positive?",
    description: "This finding will be excluded from future analyses. You can undo from the toast.",
    confirmLabel: "Mark FP",
    destructive: true,
  },
};

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
  const [confirmStatus, setConfirmStatus] = useState<string | null>(null);

  const previousStatus = finding.status;

  const applyPatch = async (status: string) => {
    setPending(true);
    try {
      const updated = await patchDeadCodeFinding(finding.id, { status });
      onUpdate(updated);
      setConfirmStatus(null);
      toast.success(`Finding ${status.replace(/_/g, " ")}`, {
        action: {
          label: "Undo",
          onClick: async () => {
            try {
              const reverted = await patchDeadCodeFinding(finding.id, { status: previousStatus });
              onUpdate(reverted);
            } catch (err) {
              toast.error(
                err instanceof Error ? `Couldn't undo: ${err.message}` : "Couldn't undo",
              );
            }
          },
        },
        duration: 6000,
      });
    } catch (err) {
      toast.error(
        err instanceof Error ? `Couldn't update finding: ${err.message}` : "Couldn't update finding",
      );
    } finally {
      setPending(false);
    }
  };

  const requestPatch = (status: string) => setConfirmStatus(status);

  const confirmConfig = confirmStatus ? STATUS_LABELS[confirmStatus] : null;

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
          aria-label={`Select finding ${finding.file_path}`}
          className="rounded border-[var(--color-border-default)]"
        />
      </td>
      <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-primary)] min-w-[200px] max-w-[480px]">
        <span className="truncate block" title={finding.file_path}>{finding.file_path}</span>
        {finding.symbol_name && (
          <span className="block truncate text-[var(--color-text-tertiary)]" title={finding.symbol_name}>{finding.symbol_name}</span>
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
      <td className="px-4 py-2.5 text-xs text-[var(--color-text-secondary)] hidden md:table-cell">
        {finding.primary_owner ?? "—"}
      </td>
      <td className="px-4 py-2.5 text-xs text-[var(--color-text-tertiary)] tabular-nums hidden md:table-cell">
        {finding.lines}
      </td>
      <td className="px-4 py-2.5 hidden sm:table-cell">
        {finding.safe_to_delete ? (
          <Badge variant="fresh">Safe</Badge>
        ) : (
          <Badge variant="default">Review</Badge>
        )}
      </td>
      <td className="px-4 py-2.5 hidden sm:table-cell">
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
          <div className="flex items-center gap-1 flex-wrap justify-end">
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => requestPatch("resolved")}
              className="h-6 px-2 text-xs text-green-500 hover:text-green-400"
              aria-label={`Resolve ${finding.file_path}`}
            >
              Resolve
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => requestPatch("acknowledged")}
              className="h-6 px-2 text-xs"
              aria-label={`Acknowledge ${finding.file_path}`}
            >
              Ack
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => requestPatch("false_positive")}
              className="h-6 px-2 text-xs text-[var(--color-text-tertiary)]"
              aria-label={`Mark ${finding.file_path} as false positive`}
            >
              FP
            </Button>
          </div>
        )}
      </td>
      {confirmConfig && (
        <ConfirmDialog
          open={confirmStatus !== null}
          onOpenChange={(o) => !o && setConfirmStatus(null)}
          title={confirmConfig.title}
          description={confirmConfig.description}
          confirmLabel={confirmConfig.confirmLabel}
          destructive={confirmConfig.destructive}
          loading={pending}
          onConfirm={() => applyPatch(confirmStatus!)}
        />
      )}
    </tr>
  );
}
