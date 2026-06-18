"use client";

import { useState } from "react";
import { GitBranch, Code2 } from "lucide-react";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { ConfirmDialog } from "../ui/confirm-dialog";
import { RowActions } from "../shared/row-actions";
import { formatConfidence } from "../lib/format";
import { cn } from "../lib/cn";
import type { DeadCodeFinding, DeadCodeStatus } from "@repowise-dev/types/dead-code";

const STATUS_LABELS: Record<
  string,
  { title: string; description: string; confirmLabel: string; destructive: boolean }
> = {
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
    description:
      "This finding will be excluded from future analyses. You can undo from the toast.",
    confirmLabel: "Mark FP",
    destructive: true,
  },
};

const STATUS_COLORS: Record<string, string> = {
  open: "bg-[var(--color-error)]/10 text-[var(--color-error)] border-[var(--color-error)]/20",
  acknowledged:
    "bg-[var(--color-warning)]/10 text-[var(--color-warning)] border-[var(--color-warning)]/20",
  resolved:
    "bg-[var(--color-success)]/10 text-[var(--color-success)] border-[var(--color-success)]/20",
  false_positive:
    "bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] border-[var(--color-border-default)]",
};

export interface FindingRowProps {
  finding: DeadCodeFinding;
  /** Repo base path used to build the Graph / Symbol action links. */
  repoId: string;
  selected: boolean;
  onToggle: (id: string) => void;
  /** Injected mutation — returns the updated finding (host owns the API + toasts). */
  onPatch: (id: string, patch: { status: DeadCodeStatus }) => Promise<DeadCodeFinding>;
  onUpdate: (updated: DeadCodeFinding) => void;
}

/**
 * Pure dead-code findings row: presentation + row-level status actions. The host
 * injects the patch mutation so this component carries no data/transport deps.
 */
export function FindingRow({ finding, repoId, selected, onToggle, onPatch, onUpdate }: FindingRowProps) {
  const [pending, setPending] = useState(false);
  const [confirmStatus, setConfirmStatus] = useState<string | null>(null);

  const applyPatch = async (status: string) => {
    setPending(true);
    try {
      const updated = await onPatch(finding.id, { status: status as DeadCodeStatus });
      onUpdate(updated);
      setConfirmStatus(null);
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
        <span className="truncate block" title={finding.file_path}>
          {finding.file_path}
        </span>
        {finding.symbol_name && (
          <span className="block truncate text-[var(--color-text-tertiary)]" title={finding.symbol_name}>
            {finding.symbol_name}
          </span>
        )}
      </td>
      <td className="px-4 py-2.5 text-xs text-[var(--color-text-secondary)] tabular-nums">
        <span
          className={cn(
            "font-medium",
            finding.confidence >= 0.8
              ? "text-[var(--color-error)]"
              : finding.confidence >= 0.6
                ? "text-[var(--color-warning)]"
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
          <Badge variant="fresh">Candidate</Badge>
        ) : (
          <Badge
            variant="default"
            title={
              finding.risk_factors && finding.risk_factors.length > 0
                ? `Runtime-load risk (${finding.risk_factors.join(", ")}) — verify it isn't loaded outside static imports before deleting`
                : "Lower confidence — verify before deleting"
            }
          >
            Review
          </Badge>
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
        <div className="flex items-center gap-1 flex-wrap justify-end">
          <RowActions
            actions={[
              {
                icon: GitBranch,
                label: "Graph",
                href: `/repos/${repoId}/architecture?view=graph&node=${encodeURIComponent(finding.file_path)}`,
              },
              ...(finding.symbol_name
                ? [{ icon: Code2, label: "Symbol", href: `/repos/${repoId}/architecture?view=symbols` }]
                : []),
            ]}
          />
          {finding.status === "open" && (
            <div className="flex items-center gap-1 flex-wrap justify-end">
              <Button
                size="sm"
                variant="ghost"
                disabled={pending}
                onClick={() => requestPatch("resolved")}
                className="h-6 px-2 text-xs text-[var(--color-success)] hover:text-[var(--color-success)]"
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
        </div>
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
