"use client";

import { useState, type MouseEvent } from "react";
import { GitBranch } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { ConfirmDialog } from "../ui/confirm-dialog";
import { RowActions } from "../shared/row-actions";
import { clickableRowProps, CLICKABLE_ROW_CLS } from "../shared/responsive-table";
import { AiPromptButton } from "../health/ai-prompt-button";
import { formatConfidence } from "../lib/format";
import { toFriendlyMessage } from "../lib/errors";
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
    // Deliberately does not promise exclusion from future analyses: a
    // re-analysis re-inserts every detected finding under a fresh id, so the
    // marking does not carry across passes. Upgrade path is a stable finding
    // identity server-side, then this copy can make the stronger promise.
    description: "This finding will be hidden from the list. You can undo from the toast.",
    confirmLabel: "Mark FP",
    destructive: true,
  },
};

export interface FindingRowProps {
  finding: DeadCodeFinding;
  selected: boolean;
  onToggle: (id: string) => void;
  /** Injected mutation — returns the updated finding (host owns the API + toasts). */
  onPatch: (id: string, patch: { status: DeadCodeStatus }) => Promise<DeadCodeFinding>;
  /** Href for the file detail page; the path becomes a link and the row opens it. */
  fileHref?: ((path: string) => string) | undefined;
  /** Client-side navigation for a row click (falls back to the anchor alone). */
  onNavigate?: ((href: string) => void) | undefined;
  /** Href for the dependency graph focused on this file; omit to hide the action. */
  graphHref?: ((path: string) => string) | undefined;
  /** When set, the row offers an AI cleanup prompt scoped to this finding. */
  onGeneratePrompt?: ((id: string) => void) | undefined;
}

/**
 * Pure dead-code findings row: presentation + row-level status actions. The host
 * injects the patch mutation so this component carries no data/transport deps.
 */
export function FindingRow({
  finding,
  selected,
  onToggle,
  onPatch,
  fileHref,
  onNavigate,
  graphHref,
  onGeneratePrompt,
}: FindingRowProps) {
  const [pending, setPending] = useState(false);
  const [confirmStatus, setConfirmStatus] = useState<string | null>(null);

  const applyPatch = async (status: string) => {
    setPending(true);
    try {
      await onPatch(finding.id, { status: status as DeadCodeStatus });
      setConfirmStatus(null);
    } catch (err) {
      // Without this the rejection escapes to the console and the dialog sits
      // open with no explanation of why nothing happened.
      toast.error(`Couldn't update finding: ${toFriendlyMessage(err)}`);
    } finally {
      setPending(false);
    }
  };

  const requestPatch = (status: string) => setConfirmStatus(status);
  const confirmConfig = confirmStatus ? STATUS_LABELS[confirmStatus] : null;

  const href = fileHref?.(finding.file_path);
  // Row click navigates only when the host can route; the anchor still carries
  // a real href so middle-click and "open in new tab" keep working.
  const openFile = href && onNavigate ? () => onNavigate(href) : undefined;

  // Hand a plain left click on the file name to the host router too, so it does
  // not fall through to a full page load while the row around it routes
  // client-side. Modified and non-primary clicks keep the browser's behavior.
  const onLinkClick = (e: MouseEvent<HTMLAnchorElement>) => {
    e.stopPropagation();
    if (!openFile) return;
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    openFile();
  };

  return (
    <tr
      className={cn(
        "border-b border-[var(--color-table-divider)] transition-colors last:border-0",
        selected ? "bg-[var(--color-accent-muted)]" : "hover:bg-[var(--color-bg-elevated)]",
        openFile && CLICKABLE_ROW_CLS,
      )}
      {...(openFile ? clickableRowProps(openFile) : {})}
    >
      <td className="px-4 py-2.5" onClick={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggle(finding.id)}
          aria-label={`Select finding ${finding.file_path}`}
          className="rounded border-[var(--color-border-default)]"
        />
      </td>
      <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-text-primary)] min-w-[200px] max-w-[480px]">
        {href ? (
          <a
            href={href}
            onClick={onLinkClick}
            className="truncate block hover:text-[var(--color-accent-primary)] hover:underline"
            title={finding.file_path}
          >
            {finding.file_path}
          </a>
        ) : (
          <span className="truncate block" title={finding.file_path}>
            {finding.file_path}
          </span>
        )}
        {finding.symbol_name && (
          <span className="block truncate text-[var(--color-text-tertiary)]" title={finding.symbol_name}>
            {finding.symbol_name}
          </span>
        )}
        {/* The detector's justification. It was already being fed to the AI
            prompt builder, so the model saw the reasoning and the human did not. */}
        {finding.reason && (
          <span
            className="mt-0.5 block truncate font-sans text-[11px] text-[var(--color-text-tertiary)]"
            title={finding.reason}
          >
            {finding.reason}
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
      <td className="px-4 py-2.5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-1 flex-wrap justify-end">
          {onGeneratePrompt && (
            <AiPromptButton
              variant="icon"
              label={`AI cleanup prompt for ${finding.file_path}`}
              onClick={() => onGeneratePrompt(finding.id)}
            />
          )}
          {graphHref && (
            <RowActions
              actions={[
                {
                  icon: GitBranch,
                  label: "Graph",
                  href: graphHref(finding.file_path),
                },
              ]}
            />
          )}
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
