"use client";

import {
  Folder,
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

function scoreCls(score: number): string {
  if (score >= 70) return "text-emerald-300 bg-emerald-500/10 border-emerald-500/40";
  if (score >= 40) return "text-amber-300 bg-amber-500/10 border-amber-500/40";
  return "text-red-300 bg-red-500/10 border-red-500/40";
}

export interface ModuleHealthDetailViewProps {
  module: ModuleHealthDetailModel;
  onSelectOwner?: (owner: ModuleHealthOwner) => void;
  onSelectFile?: (filePath: string) => void;
  onSelectDecision?: (decisionId: string) => void;
}

/**
 * Single-module deep view: composite health score, owner mix, hotspots,
 * decisions, and headline metrics. Mirrors {@link OwnerProfileView} in
 * tone — fast to scan, all numbers linked.
 */
export function ModuleHealthDetailView({
  module,
  onSelectOwner,
  onSelectFile,
  onSelectDecision,
}: ModuleHealthDetailViewProps) {
  const score = Math.round(module.health_score);

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <Card>
        <CardContent className="p-5">
          <div className="flex flex-wrap items-start gap-5">
            <div
              className={cn(
                "flex flex-col items-center justify-center rounded-lg border px-4 py-3 text-center min-w-[88px]",
                scoreCls(module.health_score),
              )}
            >
              <span className="text-3xl font-bold tabular-nums">{score}</span>
              <span className="text-[10px] uppercase tracking-wider opacity-70">
                health
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <h1 className="flex items-center gap-2 text-2xl font-bold text-[var(--color-text-primary)]">
                <Folder className="h-5 w-5 text-[var(--color-text-tertiary)]" />
                {module.module_path}
              </h1>
              <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                {module.file_count} files · {module.symbol_count} symbols ·{" "}
                {module.contributor_count} contributors
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {module.is_silo && <Badge variant="outdated">silo</Badge>}
                {module.min_bus_factor <= 1 && (
                  <Badge variant="outdated">bus ≤ 1</Badge>
                )}
                {module.decision_count > 0 && (
                  <Badge variant="accent">
                    {module.decision_count} decisions
                  </Badge>
                )}
              </div>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Headline
              label="Hotspots"
              value={module.hotspot_count}
              icon={<Flame className="h-3.5 w-3.5 text-orange-400" />}
              tone={module.hotspot_count > 0 ? "warn" : undefined}
            />
            <Headline
              label="Dead lines"
              value={module.dead_code_lines}
              icon={<Trash2 className="h-3.5 w-3.5 text-rose-400" />}
              tone={module.dead_code_lines > 0 ? "warn" : undefined}
            />
            <Headline
              label="Doc coverage"
              value={`${Math.round(module.doc_coverage_pct)}%`}
              icon={<BookOpen className="h-3.5 w-3.5 text-sky-400" />}
            />
            <Headline
              label="Bus med"
              value={module.median_bus_factor.toFixed(1)}
              icon={<Users className="h-3.5 w-3.5 text-violet-400" />}
            />
            <Headline
              label="Bus min"
              value={module.min_bus_factor}
              icon={<ShieldAlert className="h-3.5 w-3.5 text-red-400" />}
              tone={module.min_bus_factor <= 1 ? "danger" : undefined}
            />
            <Headline
              label="Avg churn"
              value={`${Math.round(module.avg_churn_percentile)}`}
            />
          </div>
        </CardContent>
      </Card>

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
          <CardContent className="pt-0 space-y-1.5">
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
                        pct > 80 ? "bg-amber-500" : "bg-[var(--color-accent-primary)]",
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
                <FileWarning className="h-4 w-4 text-orange-400" /> Top hotspots
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
                    className="block w-full truncate rounded px-2 py-1 text-left font-mono text-[11px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
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
                  <Lightbulb className="h-4 w-4 text-yellow-400" /> Governing decisions
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
                      className="block w-full truncate rounded px-2 py-1 text-left text-[11px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
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

function Headline({
  label,
  value,
  icon,
  tone,
}: {
  label: string;
  value: string | number;
  icon?: React.ReactNode | undefined;
  tone?: "warn" | "danger" | undefined;
}) {
  const color =
    tone === "danger"
      ? "text-red-400"
      : tone === "warn"
        ? "text-orange-300"
        : "text-[var(--color-text-primary)]";
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {icon}
        {label}
      </div>
      <div className={`mt-1 text-xl font-bold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}
