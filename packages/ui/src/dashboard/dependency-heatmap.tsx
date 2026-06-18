"use client";

import { useMemo, useState, useCallback, Fragment } from "react";
import { Grid3X3 } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import type { ModuleGraph } from "@repowise-dev/types/graph";

interface DependencyHeatmapProps {
  moduleGraph: ModuleGraph;
}

function heatColor(count: number, max: number): string {
  if (count === 0) return "transparent";
  const t = Math.min(1, count / Math.max(max, 1));
  if (t > 0.7) return `rgba(245, 149, 32, ${0.55 + t * 0.4})`;
  if (t > 0.3) return `rgba(245, 149, 32, ${0.2 + t * 0.35})`;
  return `rgba(245, 149, 32, ${0.08 + t * 0.18})`;
}

function displayLabel(moduleId: string): string {
  let label = moduleId;
  if (label.startsWith("external:")) label = label.slice(9);
  const last = label.split("/").pop() ?? label;
  return last.length > 14 ? last.slice(0, 13) + "…" : last;
}

export function DependencyHeatmap({ moduleGraph }: DependencyHeatmapProps) {
  const [hover, setHover] = useState<{
    row: number;
    col: number;
    x: number;
    y: number;
  } | null>(null);

  const { modules, matrix, maxCount } = useMemo(() => {
    const connectedSet = new Set<string>();
    for (const e of moduleGraph.edges) {
      connectedSet.add(e.source);
      connectedSet.add(e.target);
    }

    const mods = moduleGraph.nodes
      .filter((n) => connectedSet.has(n.module_id))
      .sort((a, b) => b.avg_pagerank - a.avg_pagerank)
      .slice(0, 15)
      .map((m) => m.module_id);

    const modIdx = new Map<string, number>();
    mods.forEach((m, i) => modIdx.set(m, i));

    const mat: number[][] = Array.from({ length: mods.length }, () =>
      new Array<number>(mods.length).fill(0),
    );

    let max = 0;
    for (const e of moduleGraph.edges) {
      const si = modIdx.get(e.source);
      const ti = modIdx.get(e.target);
      if (si !== undefined && ti !== undefined) {
        const row = mat[si]!;
        row[ti] = (row[ti] ?? 0) + e.edge_count;
        if (row[ti]! > max) max = row[ti]!;
      }
    }

    return { modules: mods, matrix: mat, maxCount: max };
  }, [moduleGraph]);

  const handleMouseEnter = useCallback(
    (row: number, col: number, e: React.MouseEvent) => {
      const rect = (e.currentTarget as HTMLElement)
        .closest("[data-heatmap-grid]")!
        .getBoundingClientRect();
      const cellRect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      setHover({
        row,
        col,
        x: cellRect.left - rect.left + cellRect.width / 2,
        y: cellRect.top - rect.top,
      });
    },
    [],
  );

  const handleMouseLeave = useCallback(() => setHover(null), []);

  if (modules.length < 2) {
    return (
      <EmptyState
        icon={<Grid3X3 className="h-8 w-8" />}
        title="No dependency data"
        description="Not enough modules to generate a dependency heatmap."
      />
    );
  }

  const size = modules.length;
  const hoverCount =
    hover != null ? (matrix[hover.row]?.[hover.col] ?? 0) : 0;

  return (
    <div className="relative" data-heatmap-grid>
      {/* Tooltip — only for non-zero cells */}
      {hover && hoverCount > 0 && (
        <div
          className="absolute z-20 pointer-events-none px-2.5 py-1.5 rounded-md text-xs bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)] shadow-lg whitespace-nowrap"
          style={{
            left: hover.x,
            top: hover.y - 8,
            transform: "translate(-50%, -100%)",
          }}
        >
          <span className="text-[var(--color-accent-primary)] font-medium">
            {displayLabel(modules[hover.row]!)}
          </span>
          <span className="text-[var(--color-text-tertiary)]"> → </span>
          <span className="text-[var(--color-text-primary)] font-medium">
            {displayLabel(modules[hover.col]!)}
          </span>
          <span className="text-[var(--color-text-tertiary)] ml-1.5">
            {hoverCount} {hoverCount === 1 ? "dep" : "deps"}
          </span>
        </div>
      )}

      <div className="overflow-x-auto">
        <div
          className="inline-grid gap-px"
          style={{
            gridTemplateColumns: `auto repeat(${size}, minmax(24px, 1fr))`,
            gridTemplateRows: `auto repeat(${size}, minmax(24px, 1fr))`,
            minWidth: Math.max(360, size * 32 + 100),
          }}
        >
          {/* Top-left corner — empty */}
          <div />

          {/* Column headers */}
          {modules.map((mod) => (
            <div
              key={`col-${mod}`}
              className="flex items-end justify-center pb-1.5"
              style={{ height: 100 }}
            >
              <span
                className="text-[10px] leading-tight text-[var(--color-text-tertiary)] font-mono overflow-hidden text-ellipsis"
                style={{
                  writingMode: "vertical-rl",
                  transform: "rotate(180deg)",
                  maxHeight: 90,
                }}
                title={mod}
              >
                {displayLabel(mod)}
              </span>
            </div>
          ))}

          {/* Rows */}
          {modules.map((rowMod, r) => {
            const row = matrix[r]!;
            return (
              <Fragment key={rowMod}>
                {/* Row label */}
                <div className="flex items-center justify-end pr-2.5 min-w-[60px]">
                  <span
                    className="text-xs text-[var(--color-text-tertiary)] font-mono truncate max-w-[110px]"
                    title={rowMod}
                  >
                    {displayLabel(rowMod)}
                  </span>
                </div>

                {/* Cells */}
                {modules.map((colMod, c) => {
                  const count = row[c] ?? 0;
                  const isDiag = r === c;

                  return (
                    <div
                      key={`${rowMod}-${colMod}`}
                      className="aspect-square rounded-[3px] transition-opacity duration-100 cursor-default"
                      style={{
                        backgroundColor: isDiag
                          ? "rgba(255,255,255,0.03)"
                          : heatColor(count, maxCount),
                        opacity:
                          hover && hover.row !== r && hover.col !== c
                            ? 0.35
                            : 1,
                      }}
                      onMouseEnter={(e) => handleMouseEnter(r, c, e)}
                      onMouseLeave={handleMouseLeave}
                    />
                  );
                })}
              </Fragment>
            );
          })}
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-2 mt-3 pt-2 border-t border-[var(--color-border-default)]">
        <span className="text-[10px] text-[var(--color-text-tertiary)]">
          Fewer
        </span>
        <div className="flex gap-0.5">
          {[0.1, 0.3, 0.5, 0.7, 0.9].map((t) => (
            <div
              key={t}
              className="w-4 h-3 rounded-sm"
              style={{ backgroundColor: heatColor(t * maxCount, maxCount) }}
            />
          ))}
        </div>
        <span className="text-[10px] text-[var(--color-text-tertiary)]">
          More dependencies
        </span>
      </div>
    </div>
  );
}
