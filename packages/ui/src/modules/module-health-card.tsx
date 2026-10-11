"use client";

import { Folder, Flame, Trash2, BookOpen, Users, ShieldAlert, Lightbulb } from "lucide-react";
import type { ModuleHealthSummary } from "@repowise-dev/types/modules";
import { Card, CardContent } from "../ui/card";
import { Badge } from "../ui/badge";
import { cn } from "../lib/cn";
import { HealthChip, MetricTile } from "./module-helpers";

interface ModuleHealthCardProps {
  module: ModuleHealthSummary;
  onClick?: ((m: ModuleHealthSummary) => void) | undefined;
  compact?: boolean | undefined;
}

/**
 * Single module health card — at-a-glance summary of churn, owners, dead
 * code, docs and decisions for one module. Sized for a 3-column grid.
 */
export function ModuleHealthCard({ module, onClick, compact }: ModuleHealthCardProps) {
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
            <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
              {module.file_count} files · {module.symbol_count} symbols
            </p>
          </div>
          <HealthChip score={module.health_score} size="sm" />
        </div>

        <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
          <MetricTile size="sm" icon={<Flame className="h-3 w-3 text-[var(--color-warning)]" />} label="Hotspots" value={module.hotspot_count} />
          <MetricTile size="sm" icon={<Trash2 className="h-3 w-3 text-[var(--color-error)]" />} label="Dead lines" value={module.dead_code_lines} />
          <MetricTile size="sm" icon={<BookOpen className="h-3 w-3 text-[var(--color-info)]" />} label="Doc" value={`${Math.round(module.doc_coverage_pct)}%`} />
          <MetricTile size="sm" icon={<Users className="h-3 w-3 text-[var(--color-accent-primary)]" />} label="Bus med" value={module.median_bus_factor.toFixed(1)} />
          <MetricTile size="sm" icon={<ShieldAlert className="h-3 w-3 text-[var(--color-error)]" />} label="Bus min" value={module.min_bus_factor} />
          <MetricTile size="sm" icon={<Lightbulb className="h-3 w-3 text-[var(--color-caution)]" />} label="Decisions" value={module.decision_count} />
        </div>

        <div className="mt-3 flex items-center justify-between text-xs">
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
