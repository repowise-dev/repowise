import * as React from "react";
import { Info, X } from "lucide-react";

import { cn } from "../lib/cn";

export type NoticeTone = "neutral" | "info";

export interface DismissibleNoticeProps {
  /**
   * The notice itself, already rendered. A notice that formats its own
   * sentence ends up with a prop per clause; passing nodes keeps the copy
   * with the surface that knows what it is announcing.
   */
  children: React.ReactNode;
  /**
   * `"info"` tints the rule and icon with the accent; `"neutral"` leaves them
   * quiet. There is deliberately no warning or error tone: those are semantic
   * states an inline methodology notice does not have, and offering them is
   * how a calm disclosure becomes an alarm.
   */
  tone?: NoticeTone;
  /**
   * An optional link or button. Rendered after the copy, in the reading flow,
   * so a keyboard user reaches it before the dismiss control.
   */
  action?: React.ReactNode;
  /**
   * Called when the reader dismisses. Omit it and no dismiss control renders,
   * which is how a permanent inline note uses the same visual.
   *
   * The component holds no dismissed state of its own: whether this notice
   * has been seen is a question about a repository and a methodology version,
   * which only the host can answer and only the host can persist.
   */
  onDismiss?: (() => void) | undefined;
  /** Accessible name for the dismiss control. */
  dismissLabel?: string;
  className?: string;
}

/**
 * A calm inline notice, dismissed by whoever owns the answer to "again?".
 *
 * Deliberately not a modal, toast, or banner: this is for telling a reader
 * something about the numbers they are already looking at, so it sits beside
 * them and never takes focus. `role="status"` rather than `role="alert"` for
 * the same reason -- a methodology change is news, not an interruption.
 *
 * Built controlled because the three hand-rolled banners that preceded it each
 * embedded their own `localStorage` read, which put the persistence key, the
 * storage failure handling, and the visual in one file and made the visual
 * untestable without a DOM storage shim. Here the host decides visibility and
 * this decides only how it looks.
 */
export function DismissibleNotice({
  children,
  tone = "neutral",
  action,
  onDismiss,
  dismissLabel = "Dismiss",
  className,
}: DismissibleNoticeProps) {
  const ink =
    tone === "info" ? "var(--color-accent-primary)" : "var(--color-text-tertiary)";

  return (
    <div
      role="status"
      className={cn(
        "flex items-start gap-2.5 border-l-2 py-2 pl-3 text-[13px] leading-relaxed text-[var(--color-text-secondary)]",
        className,
      )}
      style={{ borderColor: ink }}
    >
      <Info className="mt-0.5 h-4 w-4 shrink-0" style={{ color: ink }} aria-hidden="true" />
      {/* min-w-0 so a long sentence wraps instead of widening the row past its
          container, which is what pushes the dismiss control off screen at
          390px. */}
      <div className="min-w-0 flex-1 [text-wrap:pretty]">
        {children}
        {action && <div className="mt-2">{action}</div>}
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={dismissLabel}
          className="-mr-1 shrink-0 rounded p-1 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
