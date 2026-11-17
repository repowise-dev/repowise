import * as React from "react";
import { cn } from "../lib/cn";

/**
 * The look every form field shares (Input, the Select trigger, comboboxes):
 * a recessed well that lifts to the surface on focus, with the neutral focus
 * ring. Disabled dims the text, not the whole box, so the value stays legible.
 */
export const FIELD_CLASS =
  "rounded-md border border-[var(--color-field-border)] bg-[var(--color-field-bg)] text-[var(--color-text-primary)] transition-colors placeholder:text-[var(--color-text-tertiary)] hover:border-[var(--color-field-border-hover)] focus-visible:bg-[var(--color-bg-surface)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-focus-ring)] disabled:cursor-not-allowed disabled:text-[var(--color-text-secondary)] disabled:hover:border-[var(--color-field-border)]";

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          FIELD_CLASS,
          "box-border min-w-0 flex h-9 w-full px-3 py-1 text-sm file:border-0 file:bg-transparent file:text-sm file:font-medium",
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  },
);
Input.displayName = "Input";

export { Input };
