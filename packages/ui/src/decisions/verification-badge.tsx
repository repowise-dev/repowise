"use client";

import * as React from "react";
import { ShieldCheck, ShieldAlert, ShieldQuestion } from "lucide-react";
import { cn } from "../lib/cn";
import type { DecisionVerification } from "@repowise-dev/types/decisions";

const VERIFICATION_CONFIG: Record<
  DecisionVerification,
  { label: string; color: string; Icon: typeof ShieldCheck; title: string }
> = {
  exact: {
    label: "Verified quote",
    color: "var(--color-success)",
    Icon: ShieldCheck,
    title: "The source quote was found verbatim in the cited source.",
  },
  fuzzy: {
    label: "Fuzzy match",
    color: "var(--color-warning)",
    Icon: ShieldAlert,
    title: "A near-match of the quote was located in the source.",
  },
  unverified: {
    label: "Unverified",
    color: "var(--color-text-tertiary)",
    Icon: ShieldQuestion,
    title: "The quote could not be located in the source — treat with care.",
  },
};

export interface VerificationBadgeProps {
  verification: DecisionVerification;
  /** Compact mode shows just the icon (with the label as a tooltip). */
  iconOnly?: boolean;
  className?: string;
}

/**
 * The anti-hallucination verification tier (exact / fuzzy / unverified) for a
 * decision or evidence row.
 *
 * A mark, not a filled badge. This repeats once per row, and a tinted ground
 * plus a border plus coloured text on a per-row token tiles into stripes down
 * the table that outweigh the decision titles beside them — the argument that
 * retired `SEVERITY_CHIP`. The icon already carries the tier; the ground was
 * saying it a second time.
 */
export function VerificationBadge({ verification, iconOnly, className }: VerificationBadgeProps) {
  const config = VERIFICATION_CONFIG[verification] ?? VERIFICATION_CONFIG.unverified;
  const { label, color, Icon, title } = config;
  return (
    <span
      className={cn("inline-flex items-center gap-1.5 text-xs", className)}
      style={{ color }}
      title={title}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
      {iconOnly ? <span className="sr-only">{label}</span> : <span>{label}</span>}
    </span>
  );
}
