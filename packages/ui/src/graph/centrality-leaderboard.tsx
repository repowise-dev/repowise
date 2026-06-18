"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, Crown } from "lucide-react";
import { cn } from "../lib/cn";

export interface CentralityNode {
  node_id: string;
  label?: string;
  pagerank: number;
  betweenness?: number;
  in_degree?: number;
  out_degree?: number;
  language?: string;
}

export interface CentralityLeaderboardProps {
  nodes: CentralityNode[];
  metric?: "pagerank" | "betweenness" | "degree";
  /** Cap the rendered rows. */
  limit?: number;
  /** Click handler — typical use is selecting the node in the graph. */
  onSelect?: (node: CentralityNode) => void;
  className?: string;
}

const METRIC_LABEL: Record<string, string> = {
  pagerank: "PageRank",
  betweenness: "Betweenness",
  degree: "Degree (in+out)",
};

function score(node: CentralityNode, metric: "pagerank" | "betweenness" | "degree"): number {
  if (metric === "pagerank") return node.pagerank;
  if (metric === "betweenness") return node.betweenness ?? 0;
  return (node.in_degree ?? 0) + (node.out_degree ?? 0);
}

export function CentralityLeaderboard({
  nodes,
  metric: initialMetric = "pagerank",
  limit = 20,
  onSelect,
  className,
}: CentralityLeaderboardProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [metric, setMetric] = useState<"pagerank" | "betweenness" | "degree">(initialMetric);
  const ranked = [...nodes].sort((a, b) => score(b, metric) - score(a, metric)).slice(0, limit);
  const max = ranked.length > 0 ? score(ranked[0]!, metric) : 0;

  return (
    <aside
      className={cn(
        "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]",
        collapsed ? "w-10" : "w-[280px]",
        "transition-all overflow-hidden flex flex-col",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b border-[var(--color-border-default)] px-2 py-2">
        {!collapsed && (
          <div className="flex items-center gap-1.5 min-w-0">
            <Crown className="h-3.5 w-3.5 text-[var(--color-warning)] shrink-0" />
            <span className="text-xs font-medium text-[var(--color-text-primary)] truncate">
              Centrality
            </span>
          </div>
        )}
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="p-1 rounded hover:bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] shrink-0"
          aria-label={collapsed ? "Expand leaderboard" : "Collapse leaderboard"}
        >
          {collapsed ? <ChevronLeft className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </button>
      </div>

      {!collapsed && (
        <>
          <div className="flex border-b border-[var(--color-border-default)] text-[10px]">
            {(["pagerank", "betweenness", "degree"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMetric(m)}
                className={cn(
                  "flex-1 px-2 py-1.5 transition-colors",
                  metric === m
                    ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                    : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
                )}
              >
                {METRIC_LABEL[m]}
              </button>
            ))}
          </div>

          <ol className="flex-1 overflow-y-auto divide-y divide-[var(--color-border-default)] max-h-[480px]">
            {ranked.map((n, i) => {
              const v = score(n, metric);
              const pct = max > 0 ? (v / max) * 100 : 0;
              return (
                <li key={n.node_id}>
                  <button
                    type="button"
                    onClick={onSelect ? () => onSelect(n) : undefined}
                    disabled={!onSelect}
                    className={cn(
                      "w-full text-left px-2 py-1.5 flex items-center gap-2",
                      onSelect && "hover:bg-[var(--color-bg-elevated)] cursor-pointer",
                    )}
                    title={n.node_id}
                  >
                    <span className="text-[10px] tabular-nums text-[var(--color-text-tertiary)] w-4 text-right">
                      {i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-mono text-[var(--color-text-primary)] truncate">
                        {n.label ?? n.node_id}
                      </p>
                      <div className="mt-0.5 h-1 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
                        <div
                          className="h-full bg-[var(--color-accent-primary)]"
                          style={{ width: `${pct.toFixed(1)}%` }}
                        />
                      </div>
                    </div>
                    <span className="text-[10px] tabular-nums text-[var(--color-text-tertiary)] shrink-0">
                      {metric === "degree" ? v.toFixed(0) : v.toFixed(3)}
                    </span>
                  </button>
                </li>
              );
            })}
            {ranked.length === 0 && (
              <li className="px-3 py-4 text-xs text-[var(--color-text-tertiary)] text-center">
                No graph metrics yet.
              </li>
            )}
          </ol>
        </>
      )}
    </aside>
  );
}
