"use client";

import { Folder, Flame, Trash2, BookOpen, Users, ShieldAlert, Lightbulb } from "lucide-react";
import type { ModuleHealthSummary } from "@repowise-dev/types/modules";
import { Card, CardContent } from "../ui/card";
import { Badge } from "../ui/badge";
import { cn } from "../lib/cn";

interface ModuleHealthCardProps {
  module: ModuleHealthSummary;
  onClick?: ((m: ModuleHealthSummary) => void) | undefined;
  compact?: boolean | undefined;
}

function scoreClass(score: number): string {
  if (score >= 70) return "text-emerald-300 bg-emerald-500/10 border-emerald-500/40";
  if (score >= 40) return "text-amber-300 bg-amber-500/10 border-amber-500/40";
  return "text-red-300 bg-red-500/10 border-red-500/40";
}

/**
 * Single module health card — at-a-glance summary of churn, owners, dead
 * code, docs and decisions for one module. Sized for a 3-column grid.
 */
export function ModuleHealthCard({ module, onClick, compact }: ModuleHealthCardProps) {
  const score = Math.round(module.health_score);
  return (
    <Card
      onClick={() => onClick?.(module)}
      className={cn(
        "group transition-all",
        onClick && "cursor-pointer hover:border-[var(--color-border-hover)]",
      )}
    >
      <CardContent className={compact ? "p-3" : "p-4"}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text-primary)]">
              <Folder className="h-4 w-4 text-[var(--color-text-tertiary)]" />
              <span className="truncate">{module.module_path}</span>
            </div>
            <p className="mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">
              {module.file_count} files · {module.symbol_count} symbols
            </p>
          </div>
          <div
            className={cn(
              "rounded-md border px-2 py-1 text-center",
              scoreClass(module.health_score),
            )}
            title="Composite health score — see module health doc"
          >
            <div className="text-lg font-bold leading-none tabular-nums">{score}</div>
            <div className="text-[9px] uppercase tracking-wider opacity-70">health</div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
          <Metric icon={<Flame className="h-3 w-3 text-orange-400" />} label="Hotspots" value={module.hotspot_count} />
          <Metric icon={<Trash2 className="h-3 w-3 text-rose-400" />} label="Dead lines" value={module.dead_code_lines} />
          <Metric icon={<BookOpen className="h-3 w-3 text-sky-400" />} label="Doc" value={`${Math.round(module.doc_coverage_pct)}%`} />
          <Metric icon={<Users className="h-3 w-3 text-violet-400" />} label="Bus med" value={module.median_bus_factor.toFixed(1)} />
          <Metric icon={<ShieldAlert className="h-3 w-3 text-red-400" />} label="Bus min" value={module.min_bus_factor} />
          <Metric icon={<Lightbulb className="h-3 w-3 text-yellow-400" />} label="Decisions" value={module.decision_count} />
        </div>

        <div className="mt-3 flex items-center justify-between text-[11px]">
          <span className="truncate text-[var(--color-text-secondary)]">
            {module.primary_owner ?? "—"}
            {module.primary_owner && (
              <span className="ml-1 text-[var(--color-text-tertiary)]">
                {Math.round(module.primary_owner_pct * 100)}%
              </span>
            )}
          </span>
          <div className="flex items-center gap-1">
            {module.is_silo && (
              <Badge variant="outdated" className="text-[10px]">
                silo
              </Badge>
            )}
            <Badge variant="outline" className="text-[10px]">
              churn {Math.round(module.avg_churn_percentile)}
            </Badge>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function Metric({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
}) {
  return (
    <div>
      <div className="flex items-center gap-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {icon}
        {label}
      </div>
      <div className="tabular-nums font-semibold text-[var(--color-text-primary)]">{value}</div>
    </div>
  );
}
