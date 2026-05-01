"use client";

import { Badge } from "@repowise-dev/ui/ui/badge";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { ScrollArea } from "@repowise-dev/ui/ui/scroll-area";
import { Separator } from "@repowise-dev/ui/ui/separator";
import { useGraphMetrics, useCallersCallees } from "@/lib/hooks/use-graph";
import { truncatePath } from "@repowise-dev/ui/lib/format";
import { cn } from "@/lib/utils/cn";
import type { SymbolResponse, CallerCalleeEntry } from "@/lib/api/types";

interface SymbolGraphPanelProps {
  repoId: string;
  symbol: SymbolResponse;
}

function PercentileBadge({ value, label }: { value: number; label: string }) {
  const color =
    value >= 75
      ? "text-green-400 bg-green-400/10 border-green-400/20"
      : value >= 50
        ? "text-yellow-400 bg-yellow-400/10 border-yellow-400/20"
        : "text-[var(--color-text-tertiary)] bg-[var(--color-bg-elevated)] border-[var(--color-border-default)]";

  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[11px] text-[var(--color-text-tertiary)]">{label}</span>
      <span className={cn("text-[11px] font-mono px-1.5 py-0.5 rounded border", color)}>
        Top {100 - value}%
      </span>
    </div>
  );
}

function CallerCalleeList({
  items,
  label,
}: {
  items: CallerCalleeEntry[];
  label: string;
}) {
  if (items.length === 0) {
    return (
      <div>
        <p className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1.5">
          {label} (0)
        </p>
        <p className="text-[11px] text-[var(--color-text-tertiary)] italic">None found</p>
      </div>
    );
  }

  return (
    <div>
      <p className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1.5">
        {label} ({items.length})
      </p>
      <div className="space-y-1">
        {items.slice(0, 15).map((item) => (
          <div
            key={item.symbol_id}
            className="flex items-start gap-1.5 py-1 px-1.5 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
          >
            {/* Confidence dot */}
            <span
              className={cn(
                "mt-1.5 w-1.5 h-1.5 rounded-full shrink-0",
                item.confidence >= 0.9
                  ? "bg-green-400"
                  : item.confidence >= 0.7
                    ? "bg-yellow-400"
                    : "bg-[var(--color-text-tertiary)]",
              )}
            />
            <div className="min-w-0 flex-1">
              <p className="text-[11px] font-mono text-[var(--color-text-primary)] truncate" title={item.symbol_id}>
                {item.name}
              </p>
              <p className="text-[10px] text-[var(--color-text-tertiary)] truncate" title={item.file}>
                {truncatePath(item.file)}
              </p>
            </div>
            {item.edge_type !== "calls" && (
              <Badge variant="outline" className="text-[9px] shrink-0 h-4">
                {item.edge_type}
              </Badge>
            )}
          </div>
        ))}
        {items.length > 15 && (
          <p className="text-[10px] text-[var(--color-text-tertiary)] px-1.5">
            +{items.length - 15} more
          </p>
        )}
      </div>
    </div>
  );
}

export function SymbolGraphPanel({ repoId, symbol }: SymbolGraphPanelProps) {
  const nodeId = `${symbol.file_path}::${symbol.name}`;

  const { metrics, isLoading: metricsLoading } = useGraphMetrics(repoId, nodeId);
  const { data: callData, isLoading: callsLoading } = useCallersCallees(repoId, nodeId);
  const { data: heritageData } = useCallersCallees(repoId, nodeId, {
    edge_types: "extends,implements",
  });

  const hasHeritage =
    heritageData && (heritageData.caller_count > 0 || heritageData.callee_count > 0);

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 p-1">
        {/* Graph Metrics */}
        <div>
          <p className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
            Graph Metrics
          </p>
          {metricsLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-3/4" />
            </div>
          ) : metrics ? (
            <div className="space-y-1.5">
              <PercentileBadge value={metrics.pagerank_percentile} label="PageRank" />
              <PercentileBadge value={metrics.betweenness_percentile} label="Betweenness" />

              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-[var(--color-text-tertiary)]">Degree</span>
                <span className="text-[11px] font-mono text-[var(--color-text-secondary)]">
                  {metrics.in_degree} in &middot; {metrics.out_degree} out
                </span>
              </div>

              {metrics.community_label && (
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] text-[var(--color-text-tertiary)]">Community</span>
                  <Badge variant="outline" className="text-[10px] h-5">
                    {metrics.community_label}
                  </Badge>
                </div>
              )}

              {metrics.entry_point_score != null && metrics.entry_point_score > 0 && (
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] text-[var(--color-text-tertiary)]">Entry Point</span>
                  <div className="flex items-center gap-1.5">
                    <div className="w-12 h-1.5 rounded-full bg-[var(--color-bg-elevated)] overflow-hidden">
                      <div
                        className="h-full rounded-full bg-[var(--color-accent)]"
                        style={{ width: `${Math.round(metrics.entry_point_score * 100)}%` }}
                      />
                    </div>
                    <span className="text-[10px] font-mono text-[var(--color-text-secondary)]">
                      {(metrics.entry_point_score * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p className="text-[11px] text-[var(--color-text-tertiary)] italic">
              Not indexed in graph
            </p>
          )}
        </div>

        <Separator />

        {/* Callers & Callees */}
        <div>
          {callsLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : callData ? (
            <div className="space-y-3">
              <CallerCalleeList items={callData.callers} label="Called by" />
              <CallerCalleeList items={callData.callees} label="Calls" />
            </div>
          ) : null}
        </div>

        {/* Heritage */}
        {hasHeritage && (
          <>
            <Separator />
            <div>
              <p className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1.5">
                Heritage
              </p>
              {heritageData!.callers.length > 0 && (
                <div className="mb-2">
                  <p className="text-[10px] text-[var(--color-text-tertiary)] mb-1">Extended/Implemented by:</p>
                  {heritageData!.callers.map((c) => (
                    <p key={c.symbol_id} className="text-[11px] font-mono text-[var(--color-text-primary)] truncate pl-2">
                      {c.name}
                      <span className="text-[var(--color-text-tertiary)] ml-1">({c.edge_type})</span>
                    </p>
                  ))}
                </div>
              )}
              {heritageData!.callees.length > 0 && (
                <div>
                  <p className="text-[10px] text-[var(--color-text-tertiary)] mb-1">Extends/Implements:</p>
                  {heritageData!.callees.map((c) => (
                    <p key={c.symbol_id} className="text-[11px] font-mono text-[var(--color-text-primary)] truncate pl-2">
                      {c.name}
                      <span className="text-[var(--color-text-tertiary)] ml-1">({c.edge_type})</span>
                    </p>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </ScrollArea>
  );
}
