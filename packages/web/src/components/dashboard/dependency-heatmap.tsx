"use client";

import { useMemo, useRef, useEffect } from "react";
import type { ModuleGraphResponse } from "@/lib/api/types";

interface DependencyHeatmapProps {
  moduleGraph: ModuleGraphResponse;
}

function heatColor(count: number, max: number): string {
  if (count === 0) return "transparent";
  const intensity = Math.min(1, count / Math.max(max, 1));
  if (intensity > 0.7) return `rgba(245, 149, 32, ${0.4 + intensity * 0.5})`;
  if (intensity > 0.3) return `rgba(245, 149, 32, ${0.15 + intensity * 0.35})`;
  return `rgba(245, 149, 32, ${0.05 + intensity * 0.15})`;
}

export function DependencyHeatmap({ moduleGraph }: DependencyHeatmapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const { modules, matrix, maxCount } = useMemo(() => {
    const mods = moduleGraph.nodes
      .sort((a, b) => b.avg_pagerank - a.avg_pagerank)
      .slice(0, 20)
      .map((m) => m.module_id);

    const modIdx = new Map<string, number>();
    mods.forEach((m, i) => modIdx.set(m, i));

    const mat: number[][] = Array.from({ length: mods.length }, () =>
      new Array(mods.length).fill(0)
    );

    let max = 0;
    for (const e of moduleGraph.edges) {
      const si = modIdx.get(e.source);
      const ti = modIdx.get(e.target);
      if (si !== undefined && ti !== undefined) {
        mat[si][ti] += e.edge_count;
        if (mat[si][ti] > max) max = mat[si][ti];
      }
    }

    return { modules: mods, matrix: mat, maxCount: max };
  }, [moduleGraph]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || modules.length === 0) return;

    const size = modules.length;
    const cellSize = Math.max(14, Math.min(28, Math.floor(300 / size)));
    const labelWidth = 100;
    const padding = 4;
    const totalWidth = labelWidth + size * (cellSize + padding) + padding;
    const totalHeight = labelWidth + size * (cellSize + padding) + padding;

    canvas.width = totalWidth * 2;
    canvas.height = totalHeight * 2;
    canvas.style.width = `${totalWidth}px`;
    canvas.style.height = `${totalHeight}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(2, 2);

    // Clear
    ctx.clearRect(0, 0, totalWidth, totalHeight);

    // Draw labels (top)
    ctx.save();
    ctx.font = "9px var(--font-geist-mono, monospace)";
    ctx.fillStyle = "var(--color-text-tertiary)";
    ctx.textAlign = "right";
    for (let i = 0; i < size; i++) {
      const label = modules[i].split("/").pop() ?? modules[i];
      const x = labelWidth + i * (cellSize + padding) + cellSize / 2;
      ctx.save();
      ctx.translate(x, labelWidth - 4);
      ctx.rotate(-Math.PI / 4);
      ctx.fillText(label.slice(0, 12), 0, 0);
      ctx.restore();
    }

    // Draw labels (left)
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let i = 0; i < size; i++) {
      const label = modules[i].split("/").pop() ?? modules[i];
      const y = labelWidth + i * (cellSize + padding) + cellSize / 2;
      ctx.fillText(label.slice(0, 12), labelWidth - 6, y);
    }
    ctx.restore();

    // Draw cells
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        const count = matrix[r][c];
        const x = labelWidth + c * (cellSize + padding);
        const y = labelWidth + r * (cellSize + padding);

        if (r === c) {
          ctx.fillStyle = "rgba(255,255,255,0.03)";
        } else {
          ctx.fillStyle = heatColor(count, maxCount);
        }
        ctx.beginPath();
        ctx.roundRect(x, y, cellSize, cellSize, 2);
        ctx.fill();
      }
    }
  }, [modules, matrix, maxCount]);

  if (modules.length < 2) return null;

  return (
    <div className="overflow-x-auto">
      <canvas ref={canvasRef} />
    </div>
  );
}
