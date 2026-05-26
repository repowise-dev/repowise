"use client";

import * as React from "react";
import { ShieldCheck, ShieldAlert, ShieldQuestion } from "lucide-react";
import { Badge } from "../ui/badge";
import type { DecisionVerification } from "@repowise-dev/types/decisions";

const VERIFICATION_CONFIG: Record<
  DecisionVerification,
  { label: string; variant: "fresh" | "stale" | "default"; Icon: typeof ShieldCheck; title: string }
> = {
  exact: {
    label: "Verified quote",
    variant: "fresh",
    Icon: ShieldCheck,
    title: "The source quote was found verbatim in the cited source.",
  },
  fuzzy: {
    label: "Fuzzy match",
    variant: "stale",
    Icon: ShieldAlert,
    title: "A near-match of the quote was located in the source.",
  },
  unverified: {
    label: "Unverified",
    variant: "default",
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
 * Small trust badge for a decision/evidence row. Surfaces the anti-hallucination
 * verification tier (exact / fuzzy / unverified) so users can gauge how grounded
 * a decision's supporting evidence is.
 */
export function VerificationBadge({ verification, iconOnly, className }: VerificationBadgeProps) {
  const config = VERIFICATION_CONFIG[verification] ?? VERIFICATION_CONFIG.unverified;
  const { label, variant, Icon, title } = config;
  return (
    <Badge variant={variant} className={className} title={title}>
      <Icon className="h-3 w-3" aria-hidden />
      {!iconOnly && <span>{label}</span>}
      {iconOnly && <span className="sr-only">{label}</span>}
    </Badge>
  );
}
