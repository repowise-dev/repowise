import { AlertTriangle, RefreshCw } from "lucide-react";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";

export interface ApiErrorProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
  /** Retry button text, for hosts that translate. Default "Retry". */
  retryLabel?: string;
  /**
   * `default` centres the error in its region. `compact` is one wrapped line
   * (dot, title, sentence, retry) for a card, panel or table body that
   * should keep its own height.
   */
  size?: "default" | "compact";
  className?: string;
}

/** A failed fetch, with an optional retry. Announced as an alert. */
export function ApiError({
  title = "Failed to load",
  message = "An error occurred while fetching data.",
  onRetry,
  retryLabel = "Retry",
  size = "default",
  className,
}: ApiErrorProps) {
  if (size === "compact") {
    return (
      <div
        role="alert"
        className={cn("flex flex-wrap items-center gap-x-3 gap-y-1.5 py-3 text-xs", className)}
      >
        <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-error)]" />
        <span className="font-medium text-[var(--color-text-primary)]">{title}</span>
        <span className="min-w-0 text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {message}
        </span>
        {onRetry && (
          <Button onClick={onRetry} size="sm" variant="ghost" className="h-8 gap-1.5">
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            {retryLabel}
          </Button>
        )}
      </div>
    );
  }

  return (
    <div
      role="alert"
      className={cn("flex flex-col items-center justify-center gap-3 py-12 text-center", className)}
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--color-error-muted)]">
        <AlertTriangle className="h-5 w-5 text-[var(--color-error)]" aria-hidden />
      </div>
      <div>
        <p className="text-sm font-medium text-[var(--color-text-primary)]">{title}</p>
        <p className="mt-0.5 max-w-xs text-xs text-[var(--color-text-tertiary)]">{message}</p>
      </div>
      {onRetry && (
        <Button onClick={onRetry} size="sm" variant="secondary" className="mt-1 h-8 gap-1.5">
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          {retryLabel}
        </Button>
      )}
    </div>
  );
}
