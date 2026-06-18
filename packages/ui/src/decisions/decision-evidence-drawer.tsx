"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X, FileText, GitCommit } from "lucide-react";
import { cn } from "../lib/cn";
import { VerificationBadge } from "./verification-badge";
import type { DecisionEvidence } from "@repowise-dev/types/decisions";

const SOURCE_LABEL: Record<string, string> = {
  inline_marker: "Inline marker",
  git_archaeology: "Git archaeology",
  readme_mining: "Docs mining",
  cli: "Manual",
};

export interface DecisionEvidenceDrawerProps {
  /** Resolved evidence rows. Caller fetches; the drawer renders. */
  evidence: DecisionEvidence[] | undefined;
  isLoading?: boolean;
  error?: unknown;
  open: boolean;
  onClose: () => void;
  decisionTitle?: string;
}

/**
 * Right-side drawer listing every evidence row backing a decision, each tagged
 * with its verification tier. Makes the anti-hallucination grounding visible:
 * users can see the exact source quote and where it came from.
 */
export function DecisionEvidenceDrawer({
  evidence,
  isLoading,
  error,
  open,
  onClose,
  decisionTitle,
}: DecisionEvidenceDrawerProps) {
  // Defensive sort: API returns source_rank desc, but don't rely on it.
  const sorted = React.useMemo(
    () => (evidence ? [...evidence].sort((a, b) => b.source_rank - a.source_rank) : undefined),
    [evidence],
  );

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[1px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          className={cn(
            "fixed right-0 top-0 z-50 flex h-full w-full max-w-[560px] flex-col",
            "border-l border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shadow-2xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right",
          )}
          aria-describedby={undefined}
        >
          <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border-default)] px-4 py-3">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                Evidence
              </p>
              <DialogPrimitive.Title className="mt-0.5 break-words text-sm font-medium leading-snug text-[var(--color-text-primary)]">
                {decisionTitle ?? "Decision evidence"}
              </DialogPrimitive.Title>
            </div>
            <DialogPrimitive.Close
              aria-label="Close evidence drawer"
              className="shrink-0 rounded-md p-1.5 text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
            >
              <X className="h-4 w-4" />
            </DialogPrimitive.Close>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            {isLoading && (
              <p className="py-8 text-center text-sm text-[var(--color-text-tertiary)]">
                Loading evidence…
              </p>
            )}
            {!isLoading && Boolean(error) && (
              <p className="py-8 text-center text-sm text-[var(--color-outdated)]">
                Couldn&apos;t load evidence.
              </p>
            )}
            {!isLoading && !error && sorted && sorted.length === 0 && (
              <p className="py-8 text-center text-sm text-[var(--color-text-tertiary)]">
                No evidence rows recorded for this decision.
              </p>
            )}
            {!isLoading && !error && sorted && sorted.length > 0 && (
              <ul className="space-y-3">
                {sorted.map((row, i) => (
                  <EvidenceRow key={row.id} row={row} isPrimary={i === 0} />
                ))}
              </ul>
            )}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function EvidenceRow({ row, isPrimary }: { row: DecisionEvidence; isPrimary?: boolean }) {
  return (
    <li className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <VerificationBadge verification={row.verification} />
        <span className="text-xs text-[var(--color-text-secondary)]">
          {SOURCE_LABEL[row.source] ?? row.source}
        </span>
        {isPrimary ? (
          <span
            className="rounded bg-[var(--color-accent-muted)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-accent-primary)]"
            title="The most-trusted source backing this decision — its quote provides the headline fields. Higher rank = more trusted source type."
          >
            primary source
          </span>
        ) : (
          <span
            className="text-[10px] text-[var(--color-text-tertiary)]"
            title="Source-type trust rank. Higher ranks are more trusted; the top-ranked row is the primary source."
          >
            rank {row.source_rank}
          </span>
        )}
        <span className="ml-auto text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
          {Math.round(row.confidence * 100)}% confidence
        </span>
      </div>

      <blockquote className="rounded border border-[var(--color-border-default)] bg-[var(--color-bg-canvas,var(--color-bg-surface))] px-3 py-2 font-mono text-xs leading-relaxed text-[var(--color-text-primary)] whitespace-pre-wrap">
        {row.source_quote}
      </blockquote>

      {(row.evidence_file || row.evidence_commit) && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
          {row.evidence_file && (
            <span className="inline-flex items-center gap-1 font-mono">
              <FileText className="h-3 w-3" aria-hidden />
              {row.evidence_file}
              {row.evidence_line != null && `:${row.evidence_line}`}
            </span>
          )}
          {row.evidence_commit && (
            <span className="inline-flex items-center gap-1 font-mono">
              <GitCommit className="h-3 w-3" aria-hidden />
              {row.evidence_commit.slice(0, 8)}
            </span>
          )}
        </div>
      )}
    </li>
  );
}
