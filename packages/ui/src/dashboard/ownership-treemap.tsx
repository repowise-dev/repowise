"use client";

import { useRef, useEffect, useState } from "react";
import * as d3 from "d3-hierarchy";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../ui/tooltip";
import { Users } from "lucide-react";
import type { OwnershipEntry } from "@repowise-dev/types/git";

const OWNER_COLORS = [
  "#3b82f6", "#8b5cf6", "#06b6d4", "#f59520", "#22c55e",
  "#ec4899", "#eab308", "#ef4444", "#14b8a6", "#a855f7",
  "#6366f1", "#f97316",
];
const OWNER_FALLBACK = "#6b7280";

interface OwnershipTreemapProps {
  entries: OwnershipEntry[];
}

interface TreemapNode {
  name: string;
  value: number;
  owner: string | null;
  ownerPct: number | null;
  isSilo: boolean;
}

export function OwnershipTreemap({ entries }: OwnershipTreemapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [hoveredNode, setHoveredNode] = useState<TreemapNode | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((es) => {
      const first = es[0];
      if (!first) return;
      const { width, height } = first.contentRect;
      setDimensions({ width, height: Math.max(height, 200) });
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  if (entries.length === 0) return null;

  const owners = [...new Set(entries.map((e) => e.primary_owner).filter(Boolean))] as string[];
  const ownerColorMap = new Map<string, string>();
  owners.forEach((o, i) => ownerColorMap.set(o, OWNER_COLORS[i % OWNER_COLORS.length] ?? OWNER_FALLBACK));

  const root = d3
    .hierarchy<{ children: TreemapNode[] } | TreemapNode>({
      children: entries.map((e) => ({
        name: e.module_path,
        value: Math.max(e.file_count, 1),
        owner: e.primary_owner,
        ownerPct: e.owner_pct,
        isSilo: e.is_silo,
      })),
    } as { children: TreemapNode[] })
    .sum((d) => ("value" in d ? (d as TreemapNode).value : 0));

  const { width, height } = dimensions;

  if (width === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Users className="h-4 w-4 text-[var(--color-text-secondary)]" />
            Ownership Map
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <div ref={containerRef} className="h-[220px]" />
        </CardContent>
      </Card>
    );
  }

  const treemap = d3.treemap<{ children: TreemapNode[] } | TreemapNode>()
    .size([width, height])
    .padding(2)
    .round(true);

  treemap(root);

  const leaves = root.leaves() as Array<d3.HierarchyRectangularNode<TreemapNode>>;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Users className="h-4 w-4 text-[var(--color-text-secondary)]" />
          Ownership Map
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div ref={containerRef} className="h-[220px] relative">
          <TooltipProvider delayDuration={100}>
            <svg width={width} height={height}>
              {leaves.map((leaf) => {
                const d = leaf.data as TreemapNode;
                const color = d.owner ? (ownerColorMap.get(d.owner) ?? "var(--color-text-tertiary)") : "var(--color-text-tertiary)";
                const opacity = d.isSilo ? 0.45 : 0.75;
                const w = (leaf.x1 ?? 0) - (leaf.x0 ?? 0);
                const h = (leaf.y1 ?? 0) - (leaf.y0 ?? 0);
                return (
                  <Tooltip key={d.name}>
                    <TooltipTrigger asChild>
                      <rect
                        x={leaf.x0}
                        y={leaf.y0}
                        width={w}
                        height={h}
                        rx={3}
                        fill={color}
                        opacity={hoveredNode?.name === d.name ? 1 : opacity}
                        className="transition-opacity cursor-pointer"
                        onMouseEnter={() => setHoveredNode(d)}
                        onMouseLeave={() => setHoveredNode(null)}
                      />
                    </TooltipTrigger>
                    {w > 40 && h > 16 && (
                      <text
                        x={(leaf.x0 ?? 0) + 4}
                        y={(leaf.y0 ?? 0) + 14}
                        className="text-[10px] fill-white pointer-events-none"
                        opacity={0.9}
                      >
                        {d.name.length > w / 6 ? d.name.slice(0, Math.floor(w / 6)) + "â€¦" : d.name}
                      </text>
                    )}
                    <TooltipContent side="top" className="text-xs">
                      <p className="font-medium">{d.name}</p>
                      <p className="text-[var(--color-text-tertiary)]">
                        Owner: {d.owner ?? "none"} ({d.ownerPct ? Math.round(d.ownerPct * 100) : 0}%)
                      </p>
                      <p className="text-[var(--color-text-tertiary)]">
                        {d.value} files{d.isSilo ? " â€¢ Knowledge silo" : ""}
                      </p>
                    </TooltipContent>
                  </Tooltip>
                );
              })}
            </svg>
          </TooltipProvider>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2">
          {owners.slice(0, 6).map((owner) => (
            <div key={owner} className="flex items-center gap-1.5 text-[10px] text-[var(--color-text-secondary)]">
              <span
                className="h-2 w-2 rounded-full shrink-0"
                style={{ backgroundColor: ownerColorMap.get(owner) }}
              />
              {owner}
            </div>
          ))}
          {owners.length > 6 && (
            <span className="text-[10px] text-[var(--color-text-tertiary)]">
              +{owners.length - 6} more
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
