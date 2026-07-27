import * as React from "react";
import { cn } from "../lib/cn";
import type { DecisionStatus } from "@repowise-dev/types/decisions";

const STATUS_COLOR: Record<string, string> = {
  active: "var(--color-success)",
  proposed: "var(--color-accent-primary)",
  deprecated: "var(--color-error)",
  superseded: "var(--color-text-tertiary)",
};

export interface DecisionStatusMarkProps {
  status: DecisionStatus | string;
  className?: string;
}

/**
 * A decision's status as a dot plus the word.
 *
 * Replaces the filled `Badge`. A tinted ground, a border *and* coloured text on
 * a token that repeats once per row tiles into stripes down a table and
 * outweighs the decision titles it belongs to — the same argument that replaced
 * `SEVERITY_CHIP` with `SeverityMark`.
 *
 * The word stays. `proposed` and `active` are the pair a reader must separate
 * to use this page at all, and colour alone does not separate them for everyone.
 */
export function DecisionStatusMark({ status, className }: DecisionStatusMarkProps) {
  const color = STATUS_COLOR[status] ?? "var(--color-text-tertiary)";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap text-xs",
        className,
      )}
      style={{ color }}
    >
      <span
        aria-hidden
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: color }}
      />
      {status}
    </span>
  );
}
