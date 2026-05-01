"use client";

import { useRef, useEffect, useState, useCallback } from "react";
import { hierarchy, treemap, treemapSquarify, type HierarchyRectangularNode } from "d3-hierarchy";
import type { OwnershipEntry } from "@repowise/types/git";

interface OwnershipTreemapProps {
  entries: OwnershipEntry[];
}

const OWNER_COLORS = [
  "#5b9cf6", "#a855f7", "#22c55e", "#eab308",
  "#ec4899", "#6366f1", "#14b8a6", "#f59520",
];
const OWNER_FALLBACK = "#4b5563";

function ownerColor(name: string | null): string {
  if (!name) return OWNER_FALLBACK;
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return OWNER_COLORS[Math.abs(hash) % OWNER_COLORS.length] ?? OWNER_FALLBACK;
}

interface TooltipData {
  x: number;
  y: number;
  name: string;
  owner: string | null;
  fileCount: number;
  pct: number | null;
  isSilo: boolean;
}

export function OwnershipTreemap({ entries }: OwnershipTreemapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 600, height: 320 });
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((observed) => {
      const first = observed[0];
      if (!first) return;
      const { width } = first.contentRect;
      if (width > 0) setDims({ width, height: Math.max(240, Math.min(400, width * 0.5)) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent, d: { name: string; owner: string | null; fileCount: number; pct: number | null; isSilo: boolean }) => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      setTooltip({
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
        ...d,
      });
    },
    [],
  );

  if (entries.length === 0) return null;

  interface TreemapLeaf {
    name: string;
    value?: number;
    owner?: string | null;
    pct?: number | null;
    isSilo?: boolean;
    children?: TreemapLeaf[];
  }

  const root = hierarchy<TreemapLeaf>({
    name: "root",
    children: entries.map((e) => ({
      name: e.module_path,
      value: e.file_count,
      owner: e.primary_owner,
      pct: e.owner_pct,
      isSilo: e.is_silo,
    })),
  })
    .sum((d) => d.value || 0)
    .sort((a, b) => (b.value || 0) - (a.value || 0));

  treemap<TreemapLeaf>()
    .size([dims.width, dims.height])
    .padding(2)
    .tile(treemapSquarify)(root);

  const leaves = root.leaves() as HierarchyRectangularNode<TreemapLeaf>[];

  return (
    <div ref={containerRef} className="relative w-full">
      <svg
        width={dims.width}
        height={dims.height}
        className="rounded-lg"
        onMouseLeave={() => setTooltip(null)}
      >
        {leaves.map((leaf) => {
          const d = leaf.data;
          const x0 = leaf.x0;
          const y0 = leaf.y0;
          const w = leaf.x1 - x0;
          const h = leaf.y1 - y0;
          const showLabel = w > 50 && h > 28;

          return (
            <g
              key={d.name}
              onMouseMove={(e) =>
                handleMouseMove(e, {
                  name: d.name,
                  owner: d.owner ?? null,
                  fileCount: leaf.value || 0,
                  pct: d.pct ?? null,
                  isSilo: d.isSilo ?? false,
                })
              }
              onMouseLeave={() => setTooltip(null)}
            >
              <rect
                x={x0}
                y={y0}
                width={w}
                height={h}
                fill={ownerColor(d.owner ?? null)}
                opacity={0.8}
                rx={3}
                stroke={d.isSilo ? "#f59520" : "none"}
                strokeWidth={d.isSilo ? 2 : 0}
                strokeDasharray={d.isSilo ? "4 2" : "none"}
                className="transition-opacity hover:opacity-100"
              />
              {showLabel && (
                <>
                  <text
                    x={x0 + 6}
                    y={y0 + 14}
                    fill="#fff"
                    fontSize={11}
                    fontWeight={600}
                    fontFamily="var(--font-geist-mono)"
                    pointerEvents="none"
                  >
                    {d.name.length > w / 7 ? d.name.slice(0, Math.floor(w / 7)) + "…" : d.name}
                  </text>
                  <text
                    x={x0 + 6}
                    y={y0 + 27}
                    fill="rgba(255,255,255,0.7)"
                    fontSize={10}
                    pointerEvents="none"
                  >
                    {leaf.value} files
                  </text>
                </>
              )}
            </g>
          );
        })}
      </svg>

      {tooltip && (
        <div
          className="absolute z-20 pointer-events-none rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-3 py-2 text-xs shadow-lg"
          style={{
            left: Math.min(tooltip.x + 12, dims.width - 180),
            top: tooltip.y - 60,
          }}
        >
          <p className="font-mono font-medium text-[var(--color-text-primary)]">{tooltip.name}</p>
          <p className="text-[var(--color-text-secondary)] mt-0.5">
            Owner: {tooltip.owner ?? "—"}
            {tooltip.pct != null && ` (${Math.round(tooltip.pct * 100)}%)`}
          </p>
          <p className="text-[var(--color-text-secondary)]">{tooltip.fileCount} files</p>
          {tooltip.isSilo && (
            <p className="text-yellow-400 font-medium mt-0.5">Silo risk</p>
          )}
        </div>
      )}
    </div>
  );
}
