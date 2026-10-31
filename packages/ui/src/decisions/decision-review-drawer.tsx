"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import {
  DECISION_KIND_LABELS,
  decisionAcceptanceBlockers,
  decisionSourceLabel,
  type DecisionRecord,
} from "@repowise-dev/types/decisions";
import { cn } from "../lib/cn";
import { formatDate, stripMarkdown } from "../lib/format";
import { Button } from "../ui/button";
import { describeRecordStaleness } from "./decision-staleness";
import { VerificationBadge } from "./verification-badge";

/**
 * One record of the review queue, read in full without leaving the queue.
 *
 * The table keeps a row to one or two lines so the queue can be scanned; this
 * holds what the row cut: the whole decision and its reason, the quote it was
 * drawn from, every path it names with the hotspots marked, and why the
 * contract would refuse it. The verbs are repeated here so a reviewer can read
 * and decide in one place, and acting moves the drawer to the next record.
 *
 * Presentation only, like the rest of this directory: the host writes.
 */
export interface DecisionReviewDrawerProps {
  decision: DecisionRecord | null;
  /** Whether the review verbs apply: only a candidate is accepted or dismissed. */
  reviewable: boolean;
  onClose: () => void;
  onAccept?: ((decision: DecisionRecord) => void) | undefined;
  onDismiss?: ((decision: DecisionRecord) => void) | undefined;
  readOnlyReason?: string | undefined;
  pending?: boolean | undefined;
  /** The full record page, where its fields are edited. */
  href: string;
  Link: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  /** Slot for marks the caller already renders on the row (currency, signer). */
  marks?: React.ReactNode;
}

const MICRO_LABEL =
  "font-mono text-caption uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

export function DecisionReviewDrawer({
  decision: d,
  reviewable,
  onClose,
  onAccept,
  onDismiss,
  readOnlyReason,
  pending,
  href,
  Link,
  marks,
}: DecisionReviewDrawerProps) {
  return (
    <DialogPrimitive.Root open={d !== null} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[1px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          className={cn(
            "fixed right-0 top-0 z-50 flex h-full w-full max-w-[560px] flex-col",
            "border-l border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-2xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right",
          )}
          aria-describedby={undefined}
        >
          {d && (
            <DrawerBody
              d={d}
              reviewable={reviewable}
              onAccept={onAccept}
              onDismiss={onDismiss}
              readOnlyReason={readOnlyReason}
              pending={pending}
              href={href}
              Link={Link}
              marks={marks}
            />
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function DrawerBody({
  d,
  reviewable,
  onAccept,
  onDismiss,
  readOnlyReason,
  pending,
  href,
  Link,
  marks,
}: Omit<DecisionReviewDrawerProps, "decision" | "onClose"> & { d: DecisionRecord }) {
  const evidence = d.evidence_preview;
  const blockers = decisionAcceptanceBlockers(d);
  const staleness = describeRecordStaleness(d);
  const hot = new Set(d.hotspot_files ?? []);
  const scope = [...d.affected_files, ...d.affected_modules];
  const decisionText = stripMarkdown(d.decision || d.title);
  const reason = stripMarkdown(d.rationale || d.context || "");

  return (
    <>
      <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border-default)] px-5 py-4">
        <div className="min-w-0 space-y-1.5">
          <p className={MICRO_LABEL}>
            {decisionSourceLabel(d.source)} · recorded {formatDate(d.created_at)}
          </p>
          <DialogPrimitive.Title className="break-words text-base font-medium leading-snug text-[var(--color-text-primary)]">
            {stripMarkdown(d.title)}
          </DialogPrimitive.Title>
          {(marks || (d.verification && d.verification !== "exact")) && (
            <div className="flex flex-wrap items-center gap-2">
              {marks}
              {d.verification && d.verification !== "exact" && (
                <VerificationBadge verification={d.verification} />
              )}
            </div>
          )}
          {/* In the header, not a footer: the chat dock floats over the
              bottom-right corner of every overlay, which is where a footer's
              primary verb would sit. */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-1">
            {reviewable &&
              (readOnlyReason ? (
                <span className="text-xs text-[var(--color-text-tertiary)]">{readOnlyReason}</span>
              ) : (
                <div className="flex items-center gap-2">
                  {onAccept && (
                    <Button
                      size="sm"
                      disabled={pending || blockers.length > 0}
                      onClick={() => onAccept(d)}
                    >
                      {pending ? "Accepting…" : "Accept"}
                    </Button>
                  )}
                  {onDismiss && (
                    <Button variant="ghost" size="sm" disabled={pending} onClick={() => onDismiss(d)}>
                      Dismiss
                    </Button>
                  )}
                </div>
              ))}
            <Link
              href={href}
              className="text-xs text-[var(--color-text-secondary)] underline-offset-4 hover:text-[var(--color-text-primary)] hover:underline"
            >
              Open and edit the full record
            </Link>
          </div>
        </div>
        <DialogPrimitive.Close
          aria-label="Close"
          className="shrink-0 rounded-md p-1.5 text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          <X className="h-4 w-4" />
        </DialogPrimitive.Close>
      </header>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4 text-sm">
        <Field label="Decision">
          <p className="text-[var(--color-text-primary)]">{decisionText}</p>
        </Field>

        {reason && (
          <Field label="Why">
            <p className="text-[var(--color-text-secondary)]">{reason}</p>
          </Field>
        )}

        {evidence?.source_quote && (
          <Field label="Evidence">
            <figure className="space-y-1 border-l-2 border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-3 py-2">
              <blockquote className="whitespace-pre-wrap text-xs italic text-[var(--color-text-secondary)]">
                &ldquo;{stripMarkdown(evidence.source_quote)}&rdquo;
              </blockquote>
              <figcaption className={MICRO_LABEL}>
                {decisionSourceLabel(evidence.source)}
                {evidence.evidence_file ? ` · ${evidence.evidence_file}` : ""}
                {evidence.evidence_line != null ? `:${evidence.evidence_line}` : ""}
                {(d.evidence_count ?? 0) > 1 ? ` · ${(d.evidence_count ?? 0) - 1} more` : ""}
              </figcaption>
            </figure>
          </Field>
        )}

        <Field
          label={
            scope.length === 0
              ? "Scope"
              : `Scope · ${scope.length} ${scope.length === 1 ? "path" : "paths"}${hot.size ? ` · ${hot.size} ${hot.size === 1 ? "hotspot" : "hotspots"}` : ""}`
          }
        >
          {scope.length === 0 ? (
            <p className="text-[var(--color-text-secondary)]">
              {d.kind === "agreement"
                ? `${DECISION_KIND_LABELS.agreement}: governs the whole repository.`
                : "Names no file or module."}
            </p>
          ) : (
            <ul className="space-y-0.5">
              {scope.map((p) => (
                <li key={p} className="flex items-baseline gap-2 font-mono text-xs">
                  <span className="min-w-0 break-all text-[var(--color-text-secondary)]">{p}</span>
                  {hot.has(p) && (
                    <span className="shrink-0 text-[var(--color-text-tertiary)]">hotspot</span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {staleness.kind === "moved" && (
            <p className="mt-1.5 text-xs text-[var(--color-text-tertiary)]">{staleness.sentence}</p>
          )}
        </Field>

        {reviewable && onAccept && blockers.length > 0 && (
          <Field label="Cannot accept yet">
            <p className="text-xs text-[var(--color-text-secondary)]">
              {blockers.join("; ")}. Fill it in on the full record, or with{" "}
              <code className="font-mono text-xs">repowise decision confirm {d.id.slice(0, 8)}</code>.
            </p>
          </Field>
        )}
      </div>

    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className={MICRO_LABEL}>{label}</h3>
      {children}
    </section>
  );
}
