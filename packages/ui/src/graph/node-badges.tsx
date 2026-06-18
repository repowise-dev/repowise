"use client";

/**
 * NodeBadges — single source of truth for signal badge rendering.
 *
 * Used by the tooltip, the context drawer, the legend hint, and any future
 * surface that needs to communicate "this file is hot / dead / documented /
 * decision-anchored / entry-point / test" in a compact row of chips.
 *
 * Accepts a loose signals object so callers can pass in either a Sigma node
 * attribute bag, a raw backend GraphNode, or a hand-rolled subset.
 */

import { BookOpen, FlaskConical, Flame, Skull, Stamp, Zap } from "lucide-react";
import { cn } from "../lib/cn";

export interface NodeSignalInput {
  isHotspot?: boolean | null | undefined;
  isDead?: boolean | null | undefined;
  hasDoc?: boolean | null | undefined;
  hasDecision?: boolean | null | undefined;
  isEntryPoint?: boolean | null | undefined;
  isTest?: boolean | null | undefined;
}

export interface BadgeDef {
  key: keyof NodeSignalInput;
  label: string;
  Icon: typeof Flame;
  /** Tailwind palette segments — kept here so the canvas-side dot renderer
   *  in sigma/* can stay in sync via the `BADGE_COLORS` export below. */
  tone: "danger" | "warn" | "success" | "info" | "accent" | "neutral";
}

export const BADGE_DEFS: readonly BadgeDef[] = [
  { key: "isHotspot", label: "Hotspot", Icon: Flame, tone: "danger" },
  { key: "isDead", label: "Dead", Icon: Skull, tone: "neutral" },
  { key: "hasDecision", label: "Decision", Icon: Stamp, tone: "warn" },
  { key: "hasDoc", label: "Documented", Icon: BookOpen, tone: "success" },
  { key: "isEntryPoint", label: "Entry", Icon: Zap, tone: "accent" },
  { key: "isTest", label: "Test", Icon: FlaskConical, tone: "info" },
] as const;

/** Hex colors mirrored on the Sigma canvas dot renderer (see sigma-canvas). */
export const BADGE_COLORS: Record<BadgeDef["tone"], string> = {
  danger: "var(--color-error)",
  warn: "var(--color-accent-primary)",
  success: "var(--color-success)",
  info: "var(--color-plum-400)",
  accent: "var(--color-info)",
  neutral: "var(--color-text-tertiary)",
};

const TONE_CLASSES: Record<BadgeDef["tone"], string> = {
  danger: "bg-[var(--color-error)]/10 text-[var(--color-error)]",
  warn: "bg-[var(--color-warning)]/10 text-[var(--color-warning)]",
  success: "bg-[var(--color-success)]/10 text-[var(--color-success)]",
  info: "bg-[var(--color-plum-400)]/10 text-[var(--color-plum-400)]",
  accent: "bg-[var(--color-info)]/10 text-[var(--color-info)]",
  neutral: "bg-[var(--color-text-tertiary)]/10 text-[var(--color-text-tertiary)]",
};

export interface NodeBadgesProps {
  signals: NodeSignalInput;
  /** When omitted, all true signals render. Pass to filter (e.g. drawer-only). */
  only?: Array<BadgeDef["key"]>;
  size?: "xs" | "sm";
  className?: string;
}

export function NodeBadges({ signals, only, size = "xs", className }: NodeBadgesProps) {
  const defs = only
    ? BADGE_DEFS.filter((d) => only.includes(d.key))
    : BADGE_DEFS;
  const active = defs.filter((d) => Boolean(signals[d.key]));
  if (active.length === 0) return null;

  const sizeClass =
    size === "sm" ? "text-xs px-2 py-0.5" : "text-[10px] px-1.5 py-0.5";
  const iconSize = size === "sm" ? "w-3 h-3" : "w-2.5 h-2.5";

  return (
    <div className={cn("inline-flex flex-wrap items-center gap-1", className)}>
      {active.map(({ key, label, Icon, tone }) => (
        <span
          key={key}
          className={cn(
            "inline-flex items-center gap-1 rounded-md font-medium",
            TONE_CLASSES[tone],
            sizeClass,
          )}
        >
          <Icon className={iconSize} />
          {label}
        </span>
      ))}
    </div>
  );
}
