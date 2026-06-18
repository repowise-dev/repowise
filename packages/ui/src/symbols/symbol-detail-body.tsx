"use client";

import {
  AlertTriangle,
  Flame,
  GitBranch,
  Lightbulb,
  Network,
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
 * intelligence block (graph metrics, entry-point score, call graph, heritage,
 * git/owner context, co-changes, dead code, governing decisions) is data-driven:
 * it renders only when its feed is present and degrades silently when absent.
 *
 * The two surfaces differ in what they CAN supply, not in what the body can
 * render. The drawer's web wrapper streams file-level git intelligence
 * (git/co-changes/dead-code/entry-point/recent-owner/churn) from dedicated
 * endpoints, so those blocks light up there. The routed endpoint
 * (`SymbolDetailResponse`) omits file-level git intelligence by design — it
 * carries symbol, graph (callers/callees), function-blame, governing decisions,
 * and file_context — so the body simply hides the git/co-change/dead-code blocks
 * on the route rather than fabricating them. Governing decisions render here for
 * both surfaces so each fact appears exactly once.
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
  const heritage = data.heritage;
  const heritageParents = heritage?.parents ?? [];
  const heritageChildren = heritage?.children ?? [];
  const governingDecisions = data.governing_decisions ?? [];

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
          graph.community_label != null ||
          (graph.entry_point_score != null && graph.entry_point_score > 0)) && (
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
            {graph.entry_point_score != null && graph.entry_point_score > 0 && (
              <div className="mt-2.5 flex items-center justify-between gap-2 text-xs">
                <span className="text-[var(--color-text-tertiary)]">Entry point</span>
                <div className="flex items-center gap-1.5">
                  <div className="h-1.5 w-12 overflow-hidden rounded-full bg-[var(--color-bg-elevated)]">
                    <div
                      className="h-full rounded-full bg-[var(--color-accent-primary)]"
                      style={{ width: `${Math.round(graph.entry_point_score * 100)}%` }}
                    />
                  </div>
                  <span className="font-mono tabular-nums text-[var(--color-text-secondary)]">
                    {(graph.entry_point_score * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            )}
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

      {/* ── Heritage (extends / implements / extended-by) ── */}
      {(heritageParents.length > 0 || heritageChildren.length > 0) && (
        <div className="space-y-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
          <p className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            <Network className="h-3 w-3" /> Heritage
          </p>
          {heritageParents.length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] text-[var(--color-text-tertiary)]">Extends / implements</p>
              {heritageParents.map((r) => (
                <p
                  key={`${r.kind}:${r.parent_id ?? r.parent_name}:${r.line}`}
                  className="truncate pl-2 font-mono text-xs text-[var(--color-text-primary)]"
                  title={r.parent_id ?? r.parent_name}
                >
                  {r.parent_name}
                  <span className="ml-1 text-[var(--color-text-tertiary)]">({r.kind})</span>
                </p>
              ))}
            </div>
          )}
          {heritageChildren.length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] text-[var(--color-text-tertiary)]">Extended / implemented by</p>
              {heritageChildren.map((r) => (
                <p
                  key={`${r.kind}:${r.child_id ?? r.child_name}:${r.line}`}
                  className="truncate pl-2 font-mono text-xs text-[var(--color-text-primary)]"
                  title={r.child_id ?? r.child_name}
                >
                  {r.child_name}
                  <span className="ml-1 text-[var(--color-text-tertiary)]">({r.kind})</span>
                </p>
              ))}
            </div>
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
            {git.churn_percentile != null && (
              <span>churn top {100 - Math.round(git.churn_percentile)}%</span>
            )}
            {git.is_hotspot && (
              <span className="inline-flex items-center gap-0.5 rounded bg-[var(--color-error)]/15 px-1.5 py-0.5 text-[var(--color-error)]">
                <Flame className="h-3 w-3" /> hotspot
              </span>
            )}
          </div>
          {git.recent_owner_name && git.recent_owner_name !== git.primary_owner_name && (
            <p className="text-[var(--color-text-tertiary)]">
              Recent owner (90d):{" "}
              <span className="text-[var(--color-text-secondary)]">{git.recent_owner_name}</span>
            </p>
          )}
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

      {/* ── Governing decisions (rendered here so BOTH surfaces show them once) ── */}
      {governingDecisions.length > 0 && (
        <div className="space-y-1.5">
          <p className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            <Lightbulb className="h-3 w-3" /> Governing decisions
          </p>
          <ul className="space-y-1">
            {governingDecisions.slice(0, 5).map((d) => (
              <li
                key={d.id}
                className="flex items-center justify-between gap-2 text-xs text-[var(--color-text-secondary)]"
              >
                <span className="truncate" title={d.title}>
                  {d.title}
                </span>
                <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                  {d.status}
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
