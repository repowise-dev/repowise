"use client";

import {
  AlertTriangle,
  Flame,
  GitBranch,
  Radius,
  Skull,
  Users,
} from "lucide-react";
import type { SymbolDetailData } from "@repowise-dev/types/symbols";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { StatGrid, StatTile } from "../shared/stat-grid";
import { scoreBadgeClass } from "../health/tokens";
import { truncatePath } from "../lib/format";
import { cn } from "../lib/cn";
import { SymbolCallGraph } from "./symbol-call-graph";

export interface SymbolDetailBodyProps {
  data: SymbolDetailData;
  /** Build an href to a sibling symbol page (route surface). Omit in the modal. */
  symbolHref?: (symbolId: string) => string;
  /** Build an href to the parent file page. */
  fileHref?: (filePath: string) => string;
  /** Blast-radius CTA — wired by the host. */
  onOpenBlastRadius?: () => void;
  /** Loading flag for the optional graph/git feeds (modal streams them in). */
  metricsLoading?: boolean;
  className?: string;
}

function ageDays(t: number | null | undefined): number | null {
  if (!t) return null;
  return Math.max(0, Math.round((Date.now() / 1000 - t) / 86400));
}

/**
 * The single symbol-detail body rendered by BOTH the drawer (modal) and the
 * routed page. Purely presentational — no routing, no data fetching. Every
 * intelligence block (graph metrics, call graph, git, co-changes, dead code,
 * decisions) renders when present and degrades silently when absent, so the
 * route no longer loses capabilities the drawer had.
 */
export function SymbolDetailBody({
  data,
  symbolHref,
  fileHref,
  onOpenBlastRadius,
  metricsLoading,
  className,
}: SymbolDetailBodyProps) {
  const { identity: id } = data;
  const graph = data.graph;
  const git = data.git;
  const age = ageDays(data.blame_median_author_time);
  const fileTo = fileHref ? fileHref(id.file_path) : undefined;
  const coChanges = (data.co_changes ?? []).slice(0, 6);
  const deadCode = data.dead_code ?? [];

  return (
    <div className={cn("space-y-4", className)}>
      {/* ── Signature + docstring ── */}
      {data.signature && (
        <pre className="overflow-x-auto rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3 text-xs font-mono text-[var(--color-text-primary)]">
          {data.signature}
        </pre>
      )}
      {data.docstring && (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--color-text-secondary)]">
          {data.docstring}
        </p>
      )}
      {id.parent_name && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Parent:{" "}
          <span className="font-mono text-[var(--color-text-secondary)]">
            {id.parent_name}
          </span>
        </p>
      )}

      {/* ── Signals (callers/callees counts intentionally omitted — see call graph) ── */}
      <StatGrid columns={4}>
        <StatTile
          label="Importance"
          value={data.importance_score != null ? data.importance_score.toFixed(3) : "—"}
        />
        <StatTile
          label="Complexity"
          value={data.complexity_estimate != null ? String(data.complexity_estimate) : "—"}
        />
        <StatTile
          label="Modifications"
          value={data.blame_mod_count != null ? String(data.blame_mod_count) : "—"}
          {...(data.blame_recent_mod_count != null
            ? { hint: `${data.blame_recent_mod_count} recent` }
            : {})}
        />
        <StatTile label="Median age" value={age != null ? `${age}d` : "—"} />
      </StatGrid>

      {/* ── Graph metrics ── */}
      {graph &&
        (graph.pagerank_percentile != null ||
          graph.betweenness_percentile != null ||
          graph.community_label != null) && (
          <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
            <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Graph metrics
            </p>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-4">
              {graph.pagerank_percentile != null && (
                <Metric label="PageRank" value={`Top ${100 - graph.pagerank_percentile}%`} />
              )}
              {graph.betweenness_percentile != null && (
                <Metric label="Betweenness" value={`Top ${100 - graph.betweenness_percentile}%`} />
              )}
              <Metric label="Degree" value={`${graph.in_degree} in · ${graph.out_degree} out`} />
              {graph.community_label && (
                <Metric label="Community" value={graph.community_label} />
              )}
            </div>
          </div>
        )}

      {/* ── Call graph (replaces caller/callee lists) ── */}
      {graph && (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
          {metricsLoading ? (
            <p className="text-xs text-[var(--color-text-tertiary)]">Loading call graph…</p>
          ) : (
            <SymbolCallGraph
              centerName={id.name}
              callers={graph.callers}
              callees={graph.callees}
              {...(symbolHref ? { symbolHref } : {})}
            />
          )}
        </div>
      )}

      {/* ── Blame owner ── */}
      {data.blame_owner_name && (
        <p className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
          <Users className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
          Blame owner <span className="font-medium">{data.blame_owner_name}</span>
          {data.blame_owner_line_pct != null &&
            ` (${Math.round(data.blame_owner_line_pct * 100)}% of lines)`}
        </p>
      )}

      {/* ── File git context ── */}
      {git && (
        <div className="space-y-1.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3 text-xs">
          <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            File context
          </p>
          <div className="flex flex-wrap items-center gap-2 text-[var(--color-text-tertiary)]">
            {git.primary_owner_name && (
              <span className="text-[var(--color-text-secondary)]">
                {git.primary_owner_name}
                {git.primary_owner_commit_pct != null &&
                  ` (${Math.round(git.primary_owner_commit_pct * 100)}%)`}
              </span>
            )}
            {git.bus_factor != null && (
              <span
                className={cn(
                  "inline-flex items-center rounded px-1.5 py-0.5 tabular-nums",
                  git.bus_factor <= 1
                    ? "bg-[var(--color-error)]/15 text-[var(--color-error)]"
                    : git.bus_factor === 2
                      ? "bg-[var(--color-caution)]/15 text-[var(--color-caution)]"
                      : "bg-[var(--color-success)]/15 text-[var(--color-success)]",
                )}
              >
                bus {git.bus_factor}
              </span>
            )}
            {git.contributor_count != null && <span>{git.contributor_count} contributors</span>}
            {git.commit_count_90d != null && <span>{git.commit_count_90d} commits / 90d</span>}
            {git.is_hotspot && (
              <span className="inline-flex items-center gap-0.5 rounded bg-[var(--color-error)]/15 px-1.5 py-0.5 text-[var(--color-error)]">
                <Flame className="h-3 w-3" /> hotspot
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── Co-changes ── */}
      {coChanges.length > 0 && (
        <div className="space-y-1.5">
          <p className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            <GitBranch className="h-3 w-3" /> Co-changes
          </p>
          <ul className="space-y-1">
            {coChanges.map((p) => (
              <li
                key={p.file_path}
                className="flex items-center justify-between gap-2 font-mono text-xs"
              >
                <span className="truncate text-[var(--color-text-secondary)]" title={p.file_path}>
                  {truncatePath(p.file_path, 40)}
                </span>
                <span className="tabular-nums text-[var(--color-text-tertiary)]">
                  {p.co_change_count}×
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Dead code in scope ── */}
      {deadCode.length > 0 && (
        <div className="space-y-1.5">
          <p className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            <Skull className="h-3 w-3" /> Dead code in scope
          </p>
          <ul className="space-y-1">
            {deadCode.map((f) => (
              <li key={f.id} className="flex items-start gap-2 text-xs">
                <AlertTriangle
                  className={cn(
                    "mt-0.5 h-3 w-3 shrink-0",
                    f.safe_to_delete ? "text-[var(--color-error)]" : "text-[var(--color-caution)]",
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

      {/* ── Parent file ── */}
      {fileTo && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
          <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Parent file
          </span>
          <a
            href={fileTo}
            className="truncate font-mono text-xs text-[var(--color-accent-primary)] hover:underline"
          >
            {data.identity.file_path}
          </a>
          {data.file_context?.health_score != null && (
            <span
              className={`inline-flex items-baseline rounded px-1.5 py-0.5 text-xs font-bold tabular-nums ${scoreBadgeClass(data.file_context.health_score)}`}
            >
              {data.file_context.health_score.toFixed(1)}
              <span className="ml-0.5 text-[10px] font-normal opacity-70">/10</span>
            </span>
          )}
          {(data.file_context?.language || id.language) && (
            <Badge variant="outline" className="h-5 text-[10px] capitalize">
              {data.file_context?.language || id.language}
            </Badge>
          )}
        </div>
      )}

      {/* ── Blast radius CTA ── */}
      {onOpenBlastRadius && (
        <Button variant="outline" size="sm" onClick={onOpenBlastRadius}>
          <Radius className="mr-1.5 h-3.5 w-3.5" />
          View blast radius for {truncatePath(id.file_path, 28)}
        </Button>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[var(--color-text-tertiary)]">{label}</span>
      <span className="font-mono text-[var(--color-text-secondary)]">{value}</span>
    </div>
  );
}
