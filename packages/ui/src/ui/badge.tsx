import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../lib/cn";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      variant: {
        default:
          "border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]",
        fresh:
          "border-[color-mix(in_srgb,var(--color-confidence-fresh)_28%,transparent)] bg-[color-mix(in_srgb,var(--color-confidence-fresh)_12%,transparent)] text-[var(--color-confidence-fresh)]",
        stale:
          "border-[color-mix(in_srgb,var(--color-confidence-stale)_28%,transparent)] bg-[color-mix(in_srgb,var(--color-confidence-stale)_12%,transparent)] text-[var(--color-confidence-stale)]",
        outdated:
          "border-[color-mix(in_srgb,var(--color-confidence-outdated)_28%,transparent)] bg-[color-mix(in_srgb,var(--color-confidence-outdated)_12%,transparent)] text-[var(--color-confidence-outdated)]",
        accent:
          "border-[var(--color-accent-muted)] bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]",
        outline:
          "border-[var(--color-border-default)] bg-transparent text-[var(--color-text-secondary)]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <span
      className={cn(badgeVariants({ variant }), className)}
      role={variant && ["fresh", "stale", "outdated"].includes(variant) ? "status" : undefined}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
