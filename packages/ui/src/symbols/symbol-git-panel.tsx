"use client";

import {
  AlertTriangle,
  Flame,
  GitBranch,
  Radius,
  Skull,
  Users,
} from "lucide-react";
import { Button } from "../ui/button";
import { truncatePath } from "../lib/format";
import { cn } from "../lib/cn";

export interface SymbolGitPanelOwner {
  name: string;
  email?: string;
  commit_count?: number;
  pct?: number;
}

export interface SymbolGitPanelCoChange {
  file_path: string;
  co_change_count: number;
}

export interface SymbolGitPanelDeadFinding {
  id: string;
  kind: string;
  reason: string;
  confidence: number;
  lines: number;
  safe_to_delete: boolean;
}

export interface SymbolGitPanelProps {
  filePath: string;
  loading?: boolean;
  /** Git metadata bundle — null when the file has no git history. */
  git?: {
    primary_owner_name: string | null;
    primary_owner_commit_pct: number | null;
    recent_owner_name?: string | null;
    recent_owner_commit_pct?: number | null;
    bus_factor: number;
    contributor_count: number;
    commit_count_90d: number;
    is_hotspot: boolean;
    churn_percentile: number;
    top_authors?: SymbolGitPanelOwner[];
  } | null;
  coChanges?: SymbolGitPanelCoChange[];
  /** Dead-code findings whose line range overlaps the symbol. */
  deadCode?: SymbolGitPanelDeadFinding[];
  /** Optional callback for the "View blast radius" CTA. */
  onOpenBlastRadius?: () => void;
}

/**
 * Side-panel that overlays file-level git intelligence onto a symbol.
 * Lives in shared `packages/ui/` so the hosted frontend can re-use it
 * without re-implementing the layout. All data fetching stays in the
 * web-side wrapper; this component is purely presentational.
 */
export function SymbolGitPanel({
  filePath,
  loading,
  git,
  coChanges,
  deadCode,
  onOpenBlastRadius,
}: SymbolGitPanelProps) {
  const hasDead = (deadCode?.length ?? 0) > 0;
  const visibleCoChanges = (coChanges ?? []).slice(0, 6);
  const moreCoChanges = (coChanges?.length ?? 0) - visibleCoChanges.length;

  return (
    <div className="px-4 py-3 space-y-4 text-xs">
      <p className="text-[10px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
        File context
      </p>

      {loading && (
        <div className="space-y-2">
          <div className="h-3 w-24 rounded bg-[var(--color-bg-elevated)] animate-pulse" />
          <div className="h-3 w-32 rounded bg-[var(--color-bg-elevated)] animate-pulse" />
        </div>
      )}

      {!loading && !git && (
        <p className="text-[var(--color-text-tertiary)]">No git history for this file.</p>
      )}

      {git && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Users className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
            <span className="text-[var(--color-text-secondary)]">
              {git.primary_owner_name ?? "—"}
              {git.primary_owner_commit_pct != null && (
                <span className="ml-1 text-[var(--color-text-tertiary)] tabular-nums">
                  ({Math.round(git.primary_owner_commit_pct * 100)}%)
                </span>
              )}
            </span>
          </div>
          {git.recent_owner_name && git.recent_owner_name !== git.primary_owner_name && (
            <p className="text-[var(--color-text-tertiary)] pl-5">
              Recent owner (90d): {git.recent_owner_name}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2 text-[var(--color-text-tertiary)]">
            <span
              className={cn(
                "inline-flex items-center rounded px-1.5 py-0.5 tabular-nums",
                git.bus_factor <= 1
                  ? "bg-red-500/15 text-red-400"
                  : git.bus_factor === 2
                    ? "bg-yellow-500/15 text-yellow-400"
                    : "bg-green-500/15 text-green-400",
              )}
              title="Bus factor"
            >
              bus {git.bus_factor}
            </span>
            <span>{git.contributor_count} contributors</span>
            <span>{git.commit_count_90d} commits / 90d</span>
            {git.is_hotspot && (
              <span className="inline-flex items-center gap-0.5 rounded bg-red-500/15 px-1.5 py-0.5 text-red-400">
                <Flame className="h-3 w-3" />
                hotspot
              </span>
            )}
          </div>
        </div>
      )}

      {visibleCoChanges.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[10px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider flex items-center gap-1">
            <GitBranch className="h-3 w-3" />
            Co-changes
          </p>
          <ul className="space-y-1">
            {visibleCoChanges.map((p) => (
              <li
                key={p.file_path}
                className="flex items-center justify-between gap-2 font-mono text-[11px]"
              >
                <span className="truncate text-[var(--color-text-secondary)]" title={p.file_path}>
                  {truncatePath(p.file_path, 36)}
                </span>
                <span className="tabular-nums text-[var(--color-text-tertiary)]">
                  {p.co_change_count}&times;
                </span>
              </li>
            ))}
          </ul>
          {moreCoChanges > 0 && (
            <p className="text-[10px] text-[var(--color-text-tertiary)]">
              +{moreCoChanges} more
            </p>
          )}
        </div>
      )}

      {hasDead && (
        <div className="space-y-1.5">
          <p className="text-[10px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider flex items-center gap-1">
            <Skull className="h-3 w-3" />
            Dead code in scope
          </p>
          <ul className="space-y-1">
            {deadCode!.map((f) => (
              <li key={f.id} className="flex items-start gap-2 text-[11px]">
                <AlertTriangle
                  className={cn(
                    "h-3 w-3 mt-0.5 shrink-0",
                    f.safe_to_delete ? "text-red-400" : "text-yellow-400",
                  )}
                />
                <span className="text-[var(--color-text-secondary)]">
                  <span className="font-medium">{f.kind}</span>{" "}
                  <span className="text-[var(--color-text-tertiary)]">
                    — {f.reason} ({f.lines} lines)
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {onOpenBlastRadius && (
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          onClick={onOpenBlastRadius}
        >
          <Radius className="h-3.5 w-3.5 mr-1.5" />
          View blast radius for {truncatePath(filePath, 24)}
        </Button>
      )}
    </div>
  );
}
