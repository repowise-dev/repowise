import { ShieldCheck } from "lucide-react";
import { cn } from "../lib/cn.js";

/**
 * Honest credibility note for the change-risk model. The numbers are the
 * backtested leave-one-repo-out result from `docs/CHANGE_RISK.md` — we show
 * them rather than imply the score is infallible. The model edges churn-only
 * on average and is stronger on some repos; it is a ranking aid, not a verdict.
 */
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
        Change-risk is a calibrated linear model over each commit&apos;s diff shape
        (size, diffusion, author experience). Backtested leave-one-repo-out it scores{" "}
        <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
          0.772 AUC
        </span>{" "}
        vs{" "}
        <span className="tabular-nums">0.766</span> for a churn-only baseline — a
        ranking aid for review order, not a verdict. Priority is{" "}
        <span className="font-medium">relative to this repo&apos;s own distribution</span>.
      </p>
    </div>
  );
}
