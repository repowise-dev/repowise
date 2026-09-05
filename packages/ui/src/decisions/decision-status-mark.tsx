import * as React from "react";
import { cn } from "../lib/cn";
import type { DecisionStatus } from "@repowise-dev/types/decisions";

// Keyed on the shared ladder rather than on `string`, so a status added to the
// vocabulary fails a typecheck here instead of silently rendering grey.
const STATUS_COLOR: Record<DecisionStatus, string> = {
  active: "var(--color-success)",
  proposed: "var(--color-accent-primary)",
  deprecated: "var(--color-error)",
  // Muted like `superseded`: a tombstone is out of the way, not an alarm.
  dismissed: "var(--color-text-tertiary)",
  superseded: "var(--color-text-tertiary)",
};

/**
 * The status colour, for a bare dot in a list too narrow to carry the word.
 *
 * Exported for the same reason `healthBandInk` is: a second surface that keeps
 * its own copy of this table is a surface that can drift from it, and this one
 * already had — a local map in the VS Code decisions panel painted `proposed`
 * `--color-info` while this one painted it accent, so one status word had two
 * colours depending on where you were looking.
 */
export function decisionStatusColor(status: DecisionStatus | string): string {
  // `status` is an unconstrained string on the wire, so index safely: a row
  // sourced `toString` would otherwise hand a function to a style attribute.
  return Object.hasOwn(STATUS_COLOR, status)
    ? STATUS_COLOR[status as DecisionStatus]
    : "var(--color-text-tertiary)";
}

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
  const color = decisionStatusColor(status);
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
