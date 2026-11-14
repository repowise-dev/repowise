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
import { formatDate, parseDateOrNull, stripMarkdown } from "../lib/format";
import { Button } from "../ui/button";
import { describeRecordStaleness } from "./decision-staleness";
import { VerificationBadge } from "./verification-badge";
import {
  GapList,
  ReasonField,
  isReasonGap,
  type DecisionAcceptOptions,
} from "./decision-confirm";
import { DecisionAppliedLine, DecisionOrigin } from "./decision-origin";

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
  /** Accepts; `options.reason` carries a reason typed in the drawer. */
  onAccept?: ((decision: DecisionRecord, options?: DecisionAcceptOptions) => void) | undefined;
  onDismiss?: ((decision: DecisionRecord) => void) | undefined;
  /** Re-accepts a decision whose files moved. Given only where it applies. */
  onReaffirm?: ((decision: DecisionRecord) => void) | undefined;
  /** Pins or unpins a decision in force. Given only where it applies. */
  onPin?: ((decision: DecisionRecord, pinned: boolean) => void) | undefined;
  /**
   * The record's agent prompts (pin, veto, change it), rendered first in the
   * verb row: handing a change to an agent is the primary action.
   */
  prompts?: React.ReactNode;
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

/** The verb that turns a background record into a decision in force. */
export const PUT_IN_FORCE_LABEL = "Put in force";

/** Quote provenance as quiet row meta, matching the queue table. */
const EVIDENCE_META = "text-2xs text-[var(--color-text-tertiary)]";

const MICRO_LABEL =
  "font-mono text-caption uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

export function DecisionReviewDrawer({
  decision: d,
  reviewable,
  onClose,
  onAccept,
  onDismiss,
  onReaffirm,
  onPin,
  prompts,
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
              // Keyed so a reason typed for one record never carries to the next.
              key={d.id}
              d={d}
              reviewable={reviewable}
              onAccept={onAccept}
              onDismiss={onDismiss}
              onReaffirm={onReaffirm}
              onPin={onPin}
              prompts={prompts}
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

type BodyProps = Omit<DecisionReviewDrawerProps, "decision" | "onClose"> & {
  d: DecisionRecord;
};

function DrawerBody(props: BodyProps) {
  const { d, reviewable, onAccept } = props;
  const blockers = decisionAcceptanceBlockers(d);
  const reason = stripMarkdown(d.rationale || d.context || "");
  const [typed, setTyped] = React.useState("");
  const needsReason = blockers.some(isReasonGap);
  const otherGaps = blockers.filter((b) => !isReasonGap(b));
  const askReason = reviewable && !!onAccept && needsReason && otherGaps.length === 0;
  return (
    <>
      <DrawerHeader {...props} blockers={blockers} typedReason={askReason ? typed : ""} />
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4 text-sm">
        <Field label="Decision">
          <p className="text-[var(--color-text-primary)]">{stripMarkdown(d.decision || d.title)}</p>
        </Field>
        {reason && (
          <Field label="Why">
            <p className="text-[var(--color-text-secondary)]">{reason}</p>
          </Field>
        )}
        <DecisionOrigin decision={d} />
        <DecisionAppliedLine decision={d} />
        {askReason && (
          <ReasonField
            id={`drawer-reason-${d.id}`}
            value={typed}
            onChange={setTyped}
            disabled={props.pending}
          />
        )}
        <EvidenceField d={d} />
        <ScopeField d={d} />
        {reviewable && onAccept && otherGaps.length > 0 && (
          <GapList gaps={otherGaps} id={d.id} onRecordPage={false} />
        )}
      </div>
    </>
  );
}

function DrawerHeader({
  d,
  marks,
  href,
  Link,
  prompts,
  ...verbs
}: BodyProps & { blockers: string[]; typedReason: string }) {
  const flagged = d.verification && d.verification !== "exact" ? d.verification : null;
  return (
    <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border-default)] px-5 py-4">
      <div className="min-w-0 space-y-1.5">
        <p className={MICRO_LABEL}>
          {decisionSourceLabel(d.source)}
          {parseDateOrNull(d.created_at) ? ` · recorded ${formatDate(d.created_at)}` : ""}
        </p>
        <DialogPrimitive.Title className="break-words text-base font-medium leading-snug text-[var(--color-text-primary)]">
          {stripMarkdown(d.title)}
        </DialogPrimitive.Title>
        {(marks || flagged) && (
          <div className="flex flex-wrap items-center gap-2">
            {marks}
            {flagged && <VerificationBadge verification={flagged} />}
          </div>
        )}
        {/* In the header, not a footer: the chat dock floats over the
            bottom-right corner of every overlay, which is where a footer's
            primary verb would sit. */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-1">
          {prompts}
          <DrawerVerbs d={d} {...verbs} />
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
  );
}

type VerbProps = Pick<
  BodyProps,
  | "d"
  | "reviewable"
  | "onAccept"
  | "onDismiss"
  | "onReaffirm"
  | "onPin"
  | "readOnlyReason"
  | "pending"
> & { blockers: string[]; typedReason: string };

function DrawerVerbs(props: VerbProps) {
  const { d, reviewable, onReaffirm, onPin, readOnlyReason, pending } = props;
  if (!reviewable && !onReaffirm && !onPin) return null;
  if (readOnlyReason) {
    return <span className="text-xs text-[var(--color-text-tertiary)]">{readOnlyReason}</span>;
  }
  if (reviewable) return <ReviewVerbs {...props} />;
  return (
    <div className="flex items-center gap-2">
      {onPin && <PinVerb d={d} pending={pending ?? false} onPin={onPin} />}
      {onReaffirm && <ReaffirmVerb {...props} onReaffirm={onReaffirm} />}
    </div>
  );
}

/** Pin or unpin, the same words and titles wherever the verb is drawn. */
export const PIN_LABEL = "Pin";
export const UNPIN_LABEL = "Unpin";
export const PIN_TITLE = "Hold it as law: an agent asks a person before replacing it";
export const UNPIN_TITLE = "It stays in force; agents may replace it again";

/** A pinned decision's mark: the word, with the sentence on hover. */
export function PinnedMark() {
  return (
    <span
      className="inline-flex items-center whitespace-nowrap rounded-sm border border-[var(--color-border-default)] px-1.5 text-2xs font-medium text-[var(--color-text-secondary)]"
      title="Pinned: a person holds it as law, and an agent asks before replacing it."
    >
      Pinned
    </span>
  );
}

export function PinVerb({
  d,
  pending,
  onPin,
}: {
  d: DecisionRecord;
  pending: boolean;
  onPin: (decision: DecisionRecord, pinned: boolean) => void;
}) {
  const pinned = d.pinned === true;
  return (
    <Button
      variant="secondary"
      size="sm"
      disabled={pending}
      title={pinned ? UNPIN_TITLE : PIN_TITLE}
      onClick={() => onPin(d, !pinned)}
    >
      {pinned ? UNPIN_LABEL : PIN_LABEL}
    </Button>
  );
}

function ReaffirmVerb({
  d,
  pending,
  onReaffirm,
}: VerbProps & { onReaffirm: (decision: DecisionRecord) => void }) {
  return (
    <Button size="sm" disabled={pending} onClick={() => onReaffirm(d)}>
      {pending ? "Reaffirming..." : "Reaffirm"}
    </Button>
  );
}

function ReviewVerbs({ d, onAccept, onDismiss, pending, blockers, typedReason }: VerbProps) {
  // A reason typed below fills the one gap a reader can fill here.
  const reason = typedReason.trim();
  const open = blockers.filter((b) => !(reason && isReasonGap(b)));
  return (
    <div className="flex items-center gap-2">
      {onAccept && (
        <Button
          size="sm"
          disabled={pending || open.length > 0}
          onClick={() => (reason ? onAccept(d, { reason }) : onAccept(d))}
        >
          {pending ? "Putting in force…" : PUT_IN_FORCE_LABEL}
        </Button>
      )}
      {onDismiss && (
        <Button variant="ghost" size="sm" disabled={pending} onClick={() => onDismiss(d)}>
          Dismiss
        </Button>
      )}
    </div>
  );
}

function EvidenceField({ d }: { d: DecisionRecord }) {
  const evidence = d.evidence_preview;
  if (!evidence?.source_quote) return null;
  const more = (d.evidence_count ?? 0) - 1;
  const where = evidence.evidence_file
    ? ` · ${evidence.evidence_file}${evidence.evidence_line != null ? `:${evidence.evidence_line}` : ""}`
    : "";
  return (
    <Field label="Evidence">
      <figure className="space-y-1 border-l-2 border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-3 py-2">
        <blockquote className="whitespace-pre-wrap text-xs italic text-[var(--color-text-secondary)]">
          &ldquo;{stripMarkdown(evidence.source_quote)}&rdquo;
        </blockquote>
        <figcaption className={EVIDENCE_META}>
          {decisionSourceLabel(evidence.source)}
          {where}
          {more > 0 ? ` · ${more} more` : ""}
        </figcaption>
      </figure>
    </Field>
  );
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function ScopeField({ d }: { d: DecisionRecord }) {
  const hot = new Set(d.hotspot_files ?? []);
  const scope = [...d.affected_files, ...d.affected_modules];
  const staleness = describeRecordStaleness(d);
  const label =
    scope.length === 0
      ? "Scope"
      : `Scope · ${plural(scope.length, "path", "paths")}${hot.size ? ` · ${plural(hot.size, "hotspot", "hotspots")}` : ""}`;
  return (
    <Field label={label}>
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
              {hot.has(p) && <span className="shrink-0 text-[var(--color-text-tertiary)]">hotspot</span>}
            </li>
          ))}
        </ul>
      )}
      {staleness.kind === "moved" && (
        <p className="mt-1.5 text-xs text-[var(--color-text-tertiary)]">{staleness.sentence}</p>
      )}
    </Field>
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
