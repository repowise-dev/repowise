import * as React from "react";
import { cn } from "../lib/cn";

export type CalloutTone = "info" | "warning" | "error" | "success";

const DOT: Record<CalloutTone, string> = {
  info: "bg-[var(--color-info)]",
  warning: "bg-[var(--color-warning)]",
  error: "bg-[var(--color-error)]",
  success: "bg-[var(--color-success)]",
};

const WORD: Record<CalloutTone, string> = {
  info: "text-[var(--color-info)]",
  warning: "text-[var(--color-warning)]",
  error: "text-[var(--color-error)]",
  success: "text-[var(--color-success)]",
};

const DEFAULT_LABEL: Record<CalloutTone, string> = {
  info: "Note",
  warning: "Warning",
  error: "Error",
  success: "Done",
};

export interface CalloutProps {
  /** Default `info`. Colour lives only on the dot and the word. */
  tone?: CalloutTone;
  /**
   * The short word beside the dot ("Stale", "Partial", "Failed"). Defaults
   * per tone. Keep it to one or two words; the sentence carries the detail.
   */
  label?: string;
  /** The sentence, in ink. */
  children: React.ReactNode;
  /** Optional link or button, after the sentence in reading order. */
  action?: React.ReactNode;
  className?: string;
}

/**
 * A banner-weight note about the content beside it: neutral surface, one
 * hairline, a tone dot plus a short word, and the sentence in ink.
 *
 * Deliberately no tinted fill. A warning-coloured ground at banner size is
 * most of the colour on a page and reads louder than the finding it is
 * about; the dot and word carry the tone, and the word means colour is never
 * the only signal. `error` is announced as an alert, the rest as status.
 * For a dismissible methodology note use `DismissibleNotice`.
 */
export function Callout({ tone = "info", label, children, action, className }: CalloutProps) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      data-tone={tone}
      className={cn(
        "flex flex-wrap items-baseline gap-x-3 gap-y-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2.5",
        className,
      )}
    >
      <span
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em]",
          WORD[tone],
        )}
      >
        <span aria-hidden className={cn("h-1.5 w-1.5 shrink-0 rounded-full", DOT[tone])} />
        {label ?? DEFAULT_LABEL[tone]}
      </span>
      {/* basis-60 so the sentence drops under the word on a phone instead of
          squeezing into a sliver beside it. */}
      <div className="min-w-0 flex-1 basis-60 text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
        {children}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
