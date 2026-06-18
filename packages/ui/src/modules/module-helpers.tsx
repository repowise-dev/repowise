import * as React from "react";
import { Folder } from "lucide-react";
import { cn } from "../lib/cn";

/**
 * Single source of truth for module-health colour + chrome shared by the
 * module card and the module detail header. Module scores are on a 0–100
 * scale (unlike the 0–10 file-health pills in `health/tokens`), so these
 * helpers live alongside the module components rather than reusing the
 * file-score ramp.
 */

/** Colour classes for a 0–100 module health score. */
export function scoreColor(score: number): string {
  if (score >= 70)
    return "text-[var(--color-success)] bg-[var(--color-success)]/10 border-[var(--color-success)]/40";
  if (score >= 40)
    return "text-[var(--color-caution)] bg-[var(--color-caution)]/10 border-[var(--color-caution)]/40";
  return "text-[var(--color-error)] bg-[var(--color-error)]/10 border-[var(--color-error)]/40";
}

export interface HealthChipProps {
  /** Raw 0–100 health score. */
  score: number;
  /** `lg` for the detail header, `sm` for the card. */
  size?: "sm" | "lg";
  className?: string;
}

/** The composite-health score chip — one rendering for card + detail. */
export function HealthChip({ score, size = "sm", className }: HealthChipProps) {
  const rounded = Math.round(score);
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-md border text-center",
        size === "lg" ? "min-w-[88px] px-4 py-3" : "px-2 py-1",
        scoreColor(score),
        className,
      )}
      title="Composite health score"
    >
      <span
        className={cn(
          "font-bold leading-none tabular-nums",
          size === "lg" ? "text-3xl" : "text-lg",
        )}
      >
        {rounded}
      </span>
      <span
        className={cn(
          "uppercase tracking-wider opacity-70",
          size === "lg" ? "mt-1 text-xs" : "text-[10px]",
        )}
      >
        health
      </span>
    </div>
  );
}

export interface MetricTileProps {
  label: string;
  value: string | number;
  icon?: React.ReactNode;
  tone?: "warn" | "danger";
  /** `lg` for the detail header tiles, `sm` for the compact card grid. */
  size?: "sm" | "lg";
}

/** The shared module metric tile — replaces the card `Metric` + detail `Headline`. */
export function MetricTile({ label, value, icon, tone, size = "lg" }: MetricTileProps) {
  const color =
    tone === "danger"
      ? "text-[var(--color-error)]"
      : tone === "warn"
        ? "text-[var(--color-warning)]"
        : "text-[var(--color-text-primary)]";

  if (size === "sm") {
    return (
      <div>
        <div className="flex items-center gap-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
          {icon}
          {label}
        </div>
        <div className={cn("font-semibold tabular-nums", color)}>{value}</div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {icon}
        {label}
      </div>
      <div className={cn("mt-1 text-xl font-bold tabular-nums", color)}>{value}</div>
    </div>
  );
}

export interface ModuleIdentityProps {
  modulePath: string;
  fileCount: number;
  symbolCount: number;
  contributorCount?: number;
}

/** The shared "📁 path · N files · N symbols" identity line. */
export function ModuleIdentity({
  modulePath,
  fileCount,
  symbolCount,
  contributorCount,
}: ModuleIdentityProps) {
  return (
    <div className="min-w-0">
      {/* Heading semantics live on EntityHeader's <h1>; this is the styled identity. */}
      <div className="flex items-center gap-2 text-2xl font-bold text-[var(--color-text-primary)]">
        <Folder className="h-5 w-5 text-[var(--color-text-tertiary)] shrink-0" />
        <span className="truncate font-mono">{modulePath}</span>
      </div>
      <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
        {fileCount} files · {symbolCount} symbols
        {contributorCount != null && ` · ${contributorCount} contributors`}
      </p>
    </div>
  );
}
