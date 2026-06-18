"use client";

import {
  Flame,
  Trash2,
  BookOpen,
  Users,
  ShieldAlert,
  Lightbulb,
  FileWarning,
} from "lucide-react";
import type {
  ModuleHealthDetail as ModuleHealthDetailModel,
  ModuleHealthOwner,
} from "@repowise-dev/types/modules";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";
import { cn } from "../lib/cn";
import { truncatePath } from "../lib/format";
import { OwnerAvatar } from "../owners/owner-avatar";
import { EntityHeader } from "../shared/entity";
import type { BreadcrumbSegment } from "../shared/breadcrumb";
import { HealthChip, MetricTile, ModuleIdentity } from "./module-helpers";

export interface ModuleHealthDetailViewProps {
  module: ModuleHealthDetailModel;
  /** Breadcrumb back to repo › modules. Built by the web route. */
  breadcrumb?: BreadcrumbSegment[];
  LinkComponent?: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  onSelectOwner?: (owner: ModuleHealthOwner) => void;
  onSelectFile?: (filePath: string) => void;
  onSelectDecision?: (decisionId: string) => void;
}

/**
 * Single-module deep view: a standardized entity header (eyebrow, breadcrumb,
 * identity, one-line summary, primary health signal) over the owner mix,
 * hotspots, decisions, and headline metric tiles.
 */
export function ModuleHealthDetailView({
  module,
  breadcrumb,
  LinkComponent,
  onSelectOwner,
  onSelectFile,
  onSelectDecision,
}: ModuleHealthDetailViewProps) {
  const summary = moduleSummary(module);

  return (
    <div className="space-y-6">
      <EntityHeader
        eyebrow="MODULE"
        breadcrumb={breadcrumb ?? []}
        identity={
          <ModuleIdentity
            modulePath={module.module_path}
            fileCount={module.file_count}
            symbolCount={module.symbol_count}
            contributorCount={module.contributor_count}
          />
        }
        summary={summary}
        primarySignal={<HealthChip score={module.health_score} size="lg" />}
        metaBadges={
          <>
            {module.is_silo && <Badge variant="outdated">silo</Badge>}
            {module.min_bus_factor <= 1 && <Badge variant="outdated">bus ≤ 1</Badge>}
            {module.decision_count > 0 && (
              <Badge variant="accent">{module.decision_count} decisions</Badge>
            )}
          </>
        }
        {...(LinkComponent ? { LinkComponent } : {})}
      />

      {/* ── Headline metric tiles ── */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <MetricTile
          label="Hotspots"
          value={module.hotspot_count}
          icon={<Flame className="h-3.5 w-3.5 text-[var(--color-warning)]" />}
          {...(module.hotspot_count > 0 ? { tone: "warn" as const } : {})}
        />
        <MetricTile
          label="Dead lines"
          value={module.dead_code_lines}
          icon={<Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />}
          {...(module.dead_code_lines > 0 ? { tone: "warn" as const } : {})}
        />
        <MetricTile
          label="Doc coverage"
          value={`${Math.round(module.doc_coverage_pct)}%`}
          icon={<BookOpen className="h-3.5 w-3.5 text-[var(--color-info)]" />}
        />
        <MetricTile
          label="Bus med"
          value={module.median_bus_factor.toFixed(1)}
          icon={<Users className="h-3.5 w-3.5 text-[var(--color-accent-primary)]" />}
        />
        <MetricTile
          label="Bus min"
          value={module.min_bus_factor}
          icon={<ShieldAlert className="h-3.5 w-3.5 text-[var(--color-error)]" />}
          {...(module.min_bus_factor <= 1 ? { tone: "danger" as const } : {})}
        />
        <MetricTile
          label="Avg churn"
          value={`${Math.round(module.avg_churn_percentile)}`}
        />
      </div>

      {/* ── Body ── */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-1.5">
              <Users className="h-4 w-4" /> Owners
            </CardTitle>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              People who primarily own files in this module.
            </p>
          </CardHeader>
          <CardContent className="pt-0 space-y-2">
            {module.owners.length === 0 && (
              <p className="py-4 text-center text-xs text-[var(--color-text-tertiary)]">
                No ownership signal.
              </p>
            )}
            {module.owners.map((o) => {
              const pct = Math.round(o.pct * 100);
              return (
                <button
                  key={o.email ?? o.name}
                  onClick={() => onSelectOwner?.(o)}
                  className="flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left hover:bg-[var(--color-bg-elevated)]"
                >
                  <OwnerAvatar name={o.name} email={o.email} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-[var(--color-text-primary)]">
                      {o.name}
                    </div>
                    <div className="text-[10px] text-[var(--color-text-tertiary)]">
                      {o.file_count} files
                    </div>
                  </div>
                  <div className="h-1.5 w-32 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
                    <div
                      className={cn(
                        "h-full",
                        pct > 80
                          ? "bg-[var(--color-warning)]"
                          : "bg-[var(--color-accent-primary)]",
                      )}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="w-10 text-right text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
                    {pct}%
                  </span>
                </button>
              );
            })}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-1.5">
                <FileWarning className="h-4 w-4 text-[var(--color-warning)]" /> Top hotspots
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-0 space-y-1">
              {module.top_hotspots.length === 0 ? (
                <p className="py-2 text-center text-xs text-[var(--color-text-tertiary)]">
                  No hotspots in this module.
                </p>
              ) : (
                module.top_hotspots.map((p) => (
                  <button
                    key={p}
                    onClick={() => onSelectFile?.(p)}
                    className="block w-full truncate rounded px-2 py-1 text-left font-mono text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
                    title={p}
                  >
                    {truncatePath(p, 40)}
                  </button>
                ))
              )}
            </CardContent>
          </Card>

          {module.governing_decisions.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-1.5">
                  <Lightbulb className="h-4 w-4 text-[var(--color-caution)]" /> Governing decisions
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-0 space-y-1">
                {module.governing_decisions.map((d) => {
                  const id = typeof d === "string" ? d : d.id;
                  const label = typeof d === "string" ? `${d.slice(0, 16)}…` : d.title;
                  return (
                    <button
                      key={id}
                      onClick={() => onSelectDecision?.(id)}
                      title={typeof d === "string" ? undefined : `${d.title} (${d.status})`}
                      className="block w-full truncate rounded px-2 py-1 text-left text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
                    >
                      {label}
                    </button>
                  );
                })}
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Synthesised one-line "what is this" for a module — the missing summary.
 * Leads with the dominant health signal (the reason the score is what it is)
 * so the reader instantly knows where to look.
 */
function moduleSummary(m: ModuleHealthDetailModel): string {
  const lead = m.module_path.split("/").pop() || m.module_path;
  const parts: string[] = [];
  if (m.hotspot_count > 0) {
    parts.push(`${m.hotspot_count} hotspot${m.hotspot_count === 1 ? "" : "s"}`);
  }
  if (m.min_bus_factor <= 1) parts.push("bus-factor risk");
  if (m.dead_code_lines > 0) parts.push(`${m.dead_code_lines} dead lines`);
  if (parts.length === 0) {
    parts.push(`${Math.round(m.doc_coverage_pct)}% documented`);
  }
  return `The ${lead} module — ${m.file_count} files owned by ${
    m.primary_owner ?? "no clear owner"
  }. Leading signals: ${parts.join(", ")}.`;
}
