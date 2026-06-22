"use client";

import { useMemo } from "react";
import { ShieldAlert } from "lucide-react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";
import { cn } from "../lib/cn";

interface OwnershipDistributionBarProps {
  owners: OwnerListEntry[];
  /** Repo-wide contributor count (may exceed the loaded `owners`). */
  totalContributors: number;
  onSelect?: (owner: OwnerListEntry) => void;
}

// Solid categorical fills (Tailwind utilities, not raw hex — gate-safe and
// theme-consistent). Static literals so the JIT emits them.
const BAR_PALETTE = [
  "bg-rose-400",
  "bg-amber-400",
  "bg-emerald-400",
  "bg-sky-400",
  "bg-indigo-400",
  "bg-fuchsia-400",
  "bg-orange-400",
  "bg-teal-400",
  "bg-violet-400",
  "bg-cyan-400",
];

const TOP_N = 10;
const LEGEND_N = 6;

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function colorFor(key: string): string {
  return BAR_PALETTE[hash(key) % BAR_PALETTE.length] as string;
}

interface Segment {
  owner: OwnerListEntry;
  files: number;
  pct: number;
  color: string;
  busRisk: boolean;
}

/**
 * Knowledge distribution — a single proportional bar of how owned files spread
 * across the top contributors, with the long tail collapsed into "others". It
 * turns the directory's bus-factor / silo numbers into a shape: a bar dominated
 * by one or two segments is a concentration (knowledge) risk; an even spread is
 * healthy. Segments and legend entries drill into the owner profile.
 */
export function OwnershipDistributionBar({
  owners,
  totalContributors,
  onSelect,
}: OwnershipDistributionBarProps) {
  const { segments, othersFiles, othersCount, totalFiles, top3Pct, busRiskCount } =
    useMemo(() => {
      const sorted = [...owners].sort((a, b) => b.files_owned - a.files_owned);
      const total = sorted.reduce((s, o) => s + o.files_owned, 0) || 1;
      const top = sorted.slice(0, TOP_N);
      const segs: Segment[] = top.map((o) => ({
        owner: o,
        files: o.files_owned,
        pct: (o.files_owned / total) * 100,
        color: colorFor((o.email ?? o.key ?? o.name ?? "?").toLowerCase()),
        busRisk: o.bus_factor_risk_files > 0,
      }));
      const otherFiles = total - top.reduce((s, o) => s + o.files_owned, 0);
      const top3 = sorted.slice(0, 3).reduce((s, o) => s + o.files_owned, 0);
      return {
        segments: segs,
        othersFiles: otherFiles,
        othersCount: Math.max(0, sorted.length - top.length),
        totalFiles: total,
        top3Pct: Math.round((top3 / total) * 100),
        busRiskCount: owners.filter((o) => o.bus_factor_risk_files > 0).length,
      };
    }, [owners]);

  if (owners.length === 0 || totalFiles === 0) return null;

  return (
    <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
          Knowledge distribution
        </h3>
        <p className="text-xs text-[var(--color-text-secondary)]">
          Top 3 contributors own{" "}
          <span
            className={cn(
              "font-semibold tabular-nums",
              top3Pct >= 60
                ? "text-[var(--color-error)]"
                : top3Pct >= 40
                  ? "text-[var(--color-warning)]"
                  : "text-[var(--color-text-primary)]",
            )}
          >
            {top3Pct}%
          </span>{" "}
          of owned files
          {busRiskCount > 0 && (
            <>
              {" · "}
              <span className="inline-flex items-center gap-1 text-[var(--color-error)]">
                <ShieldAlert className="h-3.5 w-3.5" />
                {busRiskCount} carry bus-factor risk
              </span>
            </>
          )}
        </p>
      </div>

      {/* Proportional bar. Tiny segments still get a sliver via min-width. */}
      <div className="flex h-9 w-full overflow-hidden rounded-lg">
        {segments.map((s) => (
          <button
            key={s.owner.key}
            type="button"
            onClick={onSelect ? () => onSelect(s.owner) : undefined}
            className={cn(
              "group relative h-full min-w-[3px] border-r border-[var(--color-bg-surface)] transition-opacity hover:opacity-90 last:border-r-0",
              s.color,
              onSelect ? "cursor-pointer" : "cursor-default",
            )}
            style={{ width: `${s.pct}%` }}
            title={`${s.owner.name || s.owner.email || "unknown"} — ${s.files} files (${s.pct.toFixed(1)}%)${s.busRisk ? " · bus-factor risk" : ""}`}
            aria-label={`${s.owner.name || s.owner.email}: ${s.files} files`}
          >
            {s.busRisk && (
              <span className="absolute inset-x-0 top-0 h-1 bg-[var(--color-error)]" />
            )}
          </button>
        ))}
        {othersFiles > 0 && (
          <div
            className="h-full min-w-[3px] bg-[var(--color-bg-wash)]"
            style={{ width: `${(othersFiles / totalFiles) * 100}%` }}
            title={`${othersCount} other contributors — ${othersFiles} files`}
          />
        )}
      </div>

      {/* Legend: the loudest few owners, each a drill-in. */}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
        {segments.slice(0, LEGEND_N).map((s) => (
          <button
            key={s.owner.key}
            type="button"
            onClick={onSelect ? () => onSelect(s.owner) : undefined}
            className={cn(
              "flex items-center gap-1.5 text-xs",
              onSelect && "hover:text-[var(--color-text-primary)]",
            )}
          >
            <span className={cn("h-2.5 w-2.5 shrink-0 rounded-[3px]", s.color)} />
            <span className="max-w-[140px] truncate text-[var(--color-text-secondary)]">
              {s.owner.name || s.owner.email || "unknown"}
            </span>
            {s.busRisk && <ShieldAlert className="h-3 w-3 shrink-0 text-[var(--color-error)]" />}
            <span className="tabular-nums text-[var(--color-text-tertiary)]">
              {Math.round(s.pct)}%
            </span>
          </button>
        ))}
        {othersCount > 0 && (
          <span className="flex items-center gap-1.5 text-xs">
            <span className="h-2.5 w-2.5 shrink-0 rounded-[3px] bg-[var(--color-bg-wash)]" />
            <span className="text-[var(--color-text-tertiary)]">
              {othersCount} more
            </span>
          </span>
        )}
      </div>
    </div>
  );
}
