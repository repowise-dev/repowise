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
          "border-green-500/20 bg-green-500/10 text-green-500",
        stale:
          "border-yellow-500/20 bg-yellow-500/10 text-yellow-500",
        outdated:
          "border-red-500/20 bg-red-500/10 text-red-500",
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
