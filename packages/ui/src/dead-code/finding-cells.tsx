"use client";

import { useState, type MouseEvent } from "react";
import { GitBranch } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { ConfirmDialog } from "../ui/confirm-dialog";
import { RowActions } from "../shared/row-actions";
import { AiPromptButton } from "../health/ai-prompt-button";
import { formatConfidence } from "../lib/format";
import { toFriendlyMessage } from "../lib/errors";
import { cn } from "../lib/cn";
import {
  deadCodeConfidenceTier,
  type DeadCodeFinding,
  type DeadCodeStatus,
} from "@repowise-dev/types/dead-code";

/**
 * The cell renderers behind the findings table's columns. They live apart from
 * `findings-table.tsx` because the actions cell owns per-row state (a pending
 * patch and its confirm dialog) that a plain `render(row)` closure cannot hold.
 */

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
  open: {
    title: "Reopen finding?",
    description: "Put this finding back on the open list so cleanup picks it up again.",
    confirmLabel: "Reopen",
    destructive: false,
  },
};

/** Human labels for the status filter and the section hint beside it. */
export const DEAD_CODE_STATUS_LABELS: Record<DeadCodeStatus, string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  false_positive: "False positive",
};

export interface FindingIdentityProps {
  finding: DeadCodeFinding;
  /** Href for the file detail page; makes the path a link. */
  href?: string | undefined;
  /** Client-side navigation, so a plain click does not trigger a full page load. */
  onNavigate?: ((href: string) => void) | undefined;
}

/**
 * File path (linked when the host can route), symbol name, and the detector's
 * reason. The reason was already being fed to the AI prompt builder, so the
 * model saw the justification and the human did not.
 */
export function FindingIdentity({ finding, href, onNavigate }: FindingIdentityProps) {
  const onLinkClick = (e: MouseEvent<HTMLAnchorElement>) => {
    e.stopPropagation();
    if (!href || !onNavigate) return;
    // Modified and non-primary clicks keep the browser's own behavior, so
    // "open in new tab" and middle-click still work off the real href.
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    onNavigate(href);
  };

  return (
    <div className="min-w-0">
      {href ? (
        <a
          href={href}
          onClick={onLinkClick}
          className="block truncate font-mono text-xs text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)] hover:underline"
          title={finding.file_path}
        >
          {finding.file_path}
        </a>
      ) : (
        <span
          className="block truncate font-mono text-xs text-[var(--color-text-primary)]"
          title={finding.file_path}
        >
          {finding.file_path}
        </span>
      )}
      {finding.symbol_name && (
        <span
          className="block truncate font-mono text-xs text-[var(--color-text-tertiary)]"
          title={finding.symbol_name}
        >
          {finding.symbol_name}
        </span>
      )}
      {finding.reason && (
        <span
          className="mt-0.5 block truncate text-2xs text-[var(--color-text-tertiary)]"
          title={finding.reason}
        >
          {finding.reason}
        </span>
      )}
    </div>
  );
}

/** Confidence, coloured on the shared tier boundaries rather than local ones. */
export function FindingConfidence({ finding }: { finding: DeadCodeFinding }) {
  const tier = deadCodeConfidenceTier(finding.confidence);
  return (
    <span
      className={cn(
        "font-medium tabular-nums text-xs",
        tier === "high"
          ? "text-[var(--color-error)]"
          : tier === "medium"
            ? "text-[var(--color-warning)]"
            : "text-[var(--color-text-secondary)]",
      )}
    >
      {formatConfidence(finding.confidence)}
    </span>
  );
}

/** "Candidate" vs "Review", with the risk factors behind the tooltip. */
export function FindingSafety({ finding }: { finding: DeadCodeFinding }) {
  if (finding.safe_to_delete) return <Badge variant="fresh">Candidate</Badge>;
  return (
    <Badge
      variant="default"
      title={
        finding.risk_factors && finding.risk_factors.length > 0
          ? `Runtime-load risk (${finding.risk_factors.join(", ")}) - verify it isn't loaded outside static imports before deleting`
          : "Lower confidence - verify before deleting"
      }
    >
      Review
    </Badge>
  );
}

export interface FindingRowActionsProps {
  finding: DeadCodeFinding;
  /** Injected mutation - returns the updated finding (host owns the API + toasts). */
  onPatch: (id: string, patch: { status: DeadCodeStatus }) => Promise<DeadCodeFinding>;
  /** Href for the dependency graph focused on this file; omit to hide the action. */
  graphHref?: ((path: string) => string) | undefined;
  /** When set, the row offers an AI cleanup prompt scoped to this finding. */
  onGeneratePrompt?: ((id: string) => void) | undefined;
}

/**
 * Per-row status actions: resolve, acknowledge or mark false positive while a
 * finding is open, and the way back once it is not. Reopening used to be
 * reachable only from a toast that expires after six seconds.
 */
export function FindingRowActions({
  finding,
  onPatch,
  graphHref,
  onGeneratePrompt,
}: FindingRowActionsProps) {
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

  const confirmConfig = confirmStatus ? STATUS_LABELS[confirmStatus] : null;

  return (
    <span onClick={(e) => e.stopPropagation()}>
      <span className="flex flex-wrap items-center justify-end gap-1">
        {onGeneratePrompt && (
          <AiPromptButton
            variant="icon"
            label={`AI cleanup prompt for ${finding.file_path}`}
            onClick={() => onGeneratePrompt(finding.id)}
          />
        )}
        {graphHref && (
          <RowActions
            actions={[{ icon: GitBranch, label: "Graph", href: graphHref(finding.file_path) }]}
          />
        )}
        {finding.status === "open" ? (
          <>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => setConfirmStatus("resolved")}
              className="h-6 px-2 text-xs text-[var(--color-success)] hover:text-[var(--color-success)]"
              aria-label={`Resolve ${finding.file_path}`}
            >
              Resolve
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => setConfirmStatus("acknowledged")}
              className="h-6 px-2 text-xs"
              aria-label={`Acknowledge ${finding.file_path}`}
            >
              Ack
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => setConfirmStatus("false_positive")}
              className="h-6 px-2 text-xs text-[var(--color-text-tertiary)]"
              aria-label={`Mark ${finding.file_path} as false positive`}
            >
              FP
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            disabled={pending}
            onClick={() => setConfirmStatus("open")}
            className="h-6 px-2 text-xs"
            aria-label={`Reopen ${finding.file_path}`}
          >
            Reopen
          </Button>
        )}
      </span>
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
    </span>
  );
}
