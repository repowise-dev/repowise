import * as React from "react";
import { Check } from "lucide-react";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";

/**
 * What the empty view means, which decides how loud it is.
 *
 * - `neutral`: nothing here yet. Say what populates it and how. Never green.
 * - `positive`: the check ran and found nothing to fix. Gate it on a signal
 *   that the scan actually ran, or "never scanned" reads as an all-clear.
 * - `filtered`: data exists but the current filters hide all of it.
 * - `error`: the view could not load. Announced as an alert.
 */
export type EmptyStateTone = "neutral" | "positive" | "filtered" | "error";

/**
 * A button-shaped action. Pass `onClick` for in-page work or `href` for
 * navigation (rendered as a plain anchor; hosts that need client-side routing
 * wire `onClick` to their router instead).
 */
export type EmptyStateAction =
  | { label: string; onClick: () => void; href?: never }
  | { label: string; href: string; onClick?: never };

export interface EmptyStateProps {
  /** Bare icon; the component sizes and tints it. Not shown for `filtered`. */
  icon?: React.ReactNode;
  title: string;
  description?: React.ReactNode;
  /** Primary next step. */
  action?: EmptyStateAction;
  /** A quieter second step, rendered as a ghost button. */
  secondaryAction?: EmptyStateAction;
  /** Default `neutral`. */
  tone?: EmptyStateTone;
  /**
   * `compact` is one wrapped line for use inside a list, table or panel.
   * Defaults to `compact` for `filtered`, `default` otherwise.
   */
  size?: "default" | "compact";
  /**
   * Drop the hairline box, for an empty state that already sits inside a
   * card or table. `positive` and `compact` are always bare.
   */
  bare?: boolean;
  /** Extra content under the copy, e.g. a command hint. */
  children?: React.ReactNode;
  className?: string;
  /**
   * Heading level for the title. Defaults to `h3`, which is right where the
   * empty state stands in for a section inside one. Pass `h2` where it *is*
   * the whole panel — the file page's tab bodies do, and without it the page
   * runs `h1` (the path) straight to `h3` with nothing between.
   */
  titleAs?: "h2" | "h3";
}

function ActionButton({
  action,
  variant,
}: {
  action: EmptyStateAction;
  variant: "default" | "ghost" | "outline";
}) {
  if (action.href !== undefined) {
    return (
      <Button asChild size="sm" variant={variant} className="h-8">
        <a href={action.href}>{action.label}</a>
      </Button>
    );
  }
  return (
    <Button size="sm" variant={variant} className="h-8" onClick={action.onClick}>
      {action.label}
    </Button>
  );
}

/**
 * Success check on a success-muted circle: the one mark for "checked,
 * nothing to fix", shared by the positive empty state and inline all-clear
 * rows so good news looks the same everywhere.
 */
export function PositiveMark({
  size = "md",
  className,
}: {
  /** `sm` 16px for inline rows, `md` 32px for an empty state. */
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full bg-[var(--color-success-muted)] text-[var(--color-success)]",
        size === "sm" ? "h-4 w-4 [&_svg]:h-2.5 [&_svg]:w-2.5" : "h-8 w-8 [&_svg]:h-4 [&_svg]:w-4",
        className,
      )}
    >
      <Check strokeWidth={2.5} />
    </span>
  );
}

/**
 * A zero figure that is good news ("0 ALL CLEAR") for a stat cell. Render it
 * only when the measurement ran; an unmeasured zero is "Not measured".
 */
export function AllClearStat({
  label = "All clear",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <span className={cn("flex items-baseline gap-1.5", className)}>
      <span className="text-xl font-bold tabular-nums leading-none text-[var(--color-success)]">
        0
      </span>
      <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--color-success)]">
        {label}
      </span>
    </span>
  );
}

/**
 * The one empty / all-clear / filtered / error state for a region.
 *
 * Calm by default: neutral planes, a tertiary icon, no fill, no shadow.
 * Colour appears only where it carries meaning (green check for positive,
 * red mark for error). Copy says what populates the view and the scope it
 * covers ("No dead code across 1,240 files").
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
  secondaryAction,
  tone = "neutral",
  size,
  bare = false,
  children,
  className,
  titleAs: Heading = "h3",
}: EmptyStateProps) {
  const compact = (size ?? (tone === "filtered" ? "compact" : "default")) === "compact";
  const isError = tone === "error";

  let mark: React.ReactNode = null;
  if (tone === "positive") {
    mark = <PositiveMark size={compact ? "sm" : "md"} />;
  } else if (isError) {
    mark = icon ? (
      <span
        aria-hidden
        className={cn(
          "inline-flex shrink-0 text-[var(--color-error)]",
          compact ? "[&_svg]:h-4 [&_svg]:w-4" : "[&_svg]:h-5 [&_svg]:w-5",
        )}
      >
        {icon}
      </span>
    ) : (
      <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-error)]" />
    );
  } else if (icon && tone !== "filtered") {
    mark = (
      <span
        aria-hidden
        className={cn(
          "inline-flex shrink-0 text-[var(--color-text-tertiary)]",
          compact ? "[&_svg]:h-4 [&_svg]:w-4" : "[&_svg]:h-5 [&_svg]:w-5",
        )}
      >
        {icon}
      </span>
    );
  }

  if (compact) {
    return (
      <div
        role={isError ? "alert" : undefined}
        data-tone={tone}
        className={cn(
          "flex flex-wrap items-center justify-center gap-x-3 gap-y-1.5 px-4 py-4 text-center",
          className,
        )}
      >
        {mark}
        <Heading className="text-xs font-medium text-[var(--color-text-primary)]">{title}</Heading>
        {description && (
          <p className="min-w-0 text-xs text-[var(--color-text-secondary)] [text-wrap:pretty]">
            {description}
          </p>
        )}
        {action && <ActionButton action={action} variant="ghost" />}
        {secondaryAction && <ActionButton action={secondaryAction} variant="ghost" />}
        {children}
      </div>
    );
  }

  return (
    <div
      role={isError ? "alert" : undefined}
      data-tone={tone}
      className={cn(
        "flex flex-col items-center justify-center gap-3 px-6 py-10 text-center",
        !bare && tone !== "positive" && "rounded-lg border border-[var(--color-border-default)]",
        className,
      )}
    >
      {mark}
      <div className="max-w-[52ch] space-y-1">
        <Heading className="text-[15px] font-medium text-[var(--color-text-primary)]">
          {title}
        </Heading>
        {description && (
          <p className="text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
            {description}
          </p>
        )}
      </div>
      {children}
      {(action || secondaryAction) && (
        <div className="flex flex-wrap items-center justify-center gap-2">
          {action && (
            <ActionButton action={action} variant={tone === "neutral" ? "default" : "outline"} />
          )}
          {secondaryAction && <ActionButton action={secondaryAction} variant="ghost" />}
        </div>
      )}
    </div>
  );
}
