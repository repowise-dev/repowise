import { Loader2 } from "lucide-react";

import { cn } from "../lib/cn";

const SIZES = {
  xs: "h-3 w-3",
  sm: "h-3.5 w-3.5",
  md: "h-4 w-4",
  lg: "h-5 w-5",
  xl: "h-6 w-6",
} as const;

export interface SpinnerProps
  extends Omit<React.SVGAttributes<SVGSVGElement>, "role"> {
  size?: keyof typeof SIZES;
  /**
   * Accessible name. Omit when a status word sits next to the spinner — the
   * word is the announcement and a second one is noise; the icon is then
   * hidden from assistive tech. Pass it when the spinner stands alone.
   */
  label?: string;
}

/**
 * In-place indeterminate work under ~400ms: a button, a row, an inline
 * action. Pair it with a status word ("Saving…"), which is also what carries
 * the meaning under reduced motion, where the glyph stops turning.
 *
 * Not for page or panel content — that gets a matched `Skeleton`. Not for
 * determinate work with a real total — that gets `Progress`.
 *
 * Color inherits from the caller. Pass `text-[var(--color-accent-primary)]`
 * where the spinner is the subject rather than a detail of a control.
 */
export function Spinner({ size = "md", label, className, ...props }: SpinnerProps) {
  return (
    <Loader2
      {...(label
        ? { role: "status", "aria-label": label }
        : { "aria-hidden": true })}
      className={cn(SIZES[size], "shrink-0 motion-safe:animate-spin", className)}
      {...props}
    />
  );
}
