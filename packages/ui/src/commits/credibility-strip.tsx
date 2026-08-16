import { Info, ShieldCheck } from "lucide-react";
import { cn } from "../lib/cn";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";

/**
 * Honest note on what this score is. It used to lead with the backtested
 * 0.772-vs-0.766 AUC, which overstated the case: a churn-only baseline scores
 * 0.766 on those labels and lines-added alone scores higher than the model, so
 * the margin measures very little. See `docs/layers/CHANGE_RISK.md`. The copy
 * now says what the score tracks — diff size — rather than implying it ranks
 * danger.
 */

/** Shared copy so the strip and the compact info button never drift. */
function CredibilityCopy() {
  return (
    <>
      Change-risk is a calibrated linear model over each commit&apos;s diff shape
      (size, diffusion, author experience). In practice it tracks{" "}
      <span className="font-medium text-[var(--color-text-primary)]">
        how large a change is
      </span>
      , not where it lands: lines added alone reproduces it closely. Use it to
      sort by size, and check a file&apos;s bug-fix history for whether the ground
      is fragile. Priority is{" "}
      <span className="font-medium">relative to this repo&apos;s own distribution</span>.
    </>
  );
}

export function CredibilityStrip({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3 text-xs text-[var(--color-text-secondary)]",
        className,
      )}
    >
      <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
      <p>
        <CredibilityCopy />
      </p>
    </div>
  );
}

/**
 * Compact affordance for the same note — an info icon that reveals the model's
 * provenance on hover/focus. Used where the queue shouldn't spend prime real
 * estate on a full strip (e.g. inline beside the review-priority table header).
 */
export function CredibilityInfoButton({
  label = "How change-risk is scored",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={label}
            className={cn(
              "inline-flex items-center text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-secondary)]",
              className,
            )}
          >
            <Info className="h-3.5 w-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent className="max-w-[300px] text-xs leading-relaxed text-[var(--color-text-secondary)]">
          <CredibilityCopy />
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
