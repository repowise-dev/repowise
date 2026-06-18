"use client";

import { useMemo, useState } from "react";
import { Network, ChevronDown, ChevronUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { cn } from "../lib/cn";
import { formatNumber } from "../lib/format";
import type { ModuleNode, ModuleEdge } from "@repowise-dev/types/graph";

interface ModuleOverviewGridProps {
  nodes: ModuleNode[];
  edges: ModuleEdge[];
  repoId: string;
  linkPrefix?: string;
  /** Number of columns in the first visible row. Defaults to 4. */
  initialVisibleCols?: number;
}

export function ModuleOverviewGrid({ nodes, edges, repoId, linkPrefix, initialVisibleCols = 4 }: ModuleOverviewGridProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const [expanded, setExpanded] = useState(false);
  const modules = useMemo(() => {
    const edgeCounts = new Map<string, number>();
    for (const e of edges) {
      edgeCounts.set(e.source, (edgeCounts.get(e.source) ?? 0) + e.edge_count);
      edgeCounts.set(e.target, (edgeCounts.get(e.target) ?? 0) + e.edge_count);
    }

    return [...nodes]
      .sort((a, b) => b.file_count - a.file_count)
      .map((n) => ({
        ...n,
        label: n.module_id.split("/").pop() ?? n.module_id,
        depCount: edgeCounts.get(n.module_id) ?? 0,
      }));
  }, [nodes, edges]);

  if (modules.length === 0) return null;

  const hasMore = modules.length > initialVisibleCols;
  const visibleModules = expanded ? modules : modules.slice(0, initialVisibleCols);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Network className="h-4 w-4 text-[var(--color-text-secondary)]" />
            Architecture
            <span className="text-[10px] font-normal text-[var(--color-text-tertiary)]">
              {modules.length} modules
            </span>
          </span>
          <a
            href={`${prefix}/architecture?view=graph`}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline font-normal"
          >
            Full graph →
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {visibleModules.map((m) => {
            const coverageColor =
              m.doc_coverage_pct >= 70
                ? "bg-[var(--color-success)]"
                : m.doc_coverage_pct >= 30
                  ? "bg-[var(--color-warning)]"
                  : "bg-[var(--color-text-tertiary)]/50";

            return (
              <a
                key={m.module_id}
                href={`${prefix}/architecture?view=graph&node=${encodeURIComponent(m.module_id)}`}
                className="group rounded-lg border border-[var(--color-border-default)] p-3 hover:border-[var(--color-border-hover)] hover:bg-[var(--color-bg-elevated)] transition-colors"
              >
                <p className="text-xs font-medium text-[var(--color-text-primary)] truncate mb-2" title={m.module_id}>
                  {m.label}
                </p>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-[10px] text-[var(--color-text-tertiary)]">
                    <span>{formatNumber(m.file_count)} files</span>
                    <span>{formatNumber(m.symbol_count)} sym</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-[var(--color-bg-overlay)] overflow-hidden">
                    <div
                      className={cn("h-full rounded-full transition-all", coverageColor)}
                      style={{ width: `${Math.max(2, m.doc_coverage_pct)}%` }}
                    />
                  </div>
                  <div className="flex items-center justify-between text-[10px]">
                    <span className={cn(
                      m.doc_coverage_pct >= 70
                        ? "text-[var(--color-success)]"
                        : m.doc_coverage_pct >= 30
                          ? "text-[var(--color-warning)]"
                          : "text-[var(--color-text-tertiary)]",
                    )}>
                      {Math.round(m.doc_coverage_pct)}% docs
                    </span>
                    {m.depCount > 0 && (
                      <span className="text-[var(--color-text-tertiary)]">
                        {formatNumber(m.depCount)} deps
                      </span>
                    )}
                  </div>
                </div>
              </a>
            );
          })}
        </div>
        {hasMore && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mt-2 w-full flex items-center justify-center gap-1 rounded-md border border-[var(--color-border-default)] py-1.5 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)] transition-colors"
          >
            {expanded ? (
              <>
                <ChevronUp className="h-3 w-3" />
                Show less
              </>
            ) : (
              <>
                <ChevronDown className="h-3 w-3" />
                Show all {modules.length} modules
              </>
            )}
          </button>
        )}
      </CardContent>
    </Card>
  );
}
