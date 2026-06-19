"use client";

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { FileText, BarChart3 } from "lucide-react";
import { languageColor } from "../lib/confidence";

export interface WorkspaceGraphNodeData {
  repoId: string;
  name: string;
  fileCount: number;
  coveragePct: number;
  healthScore: number;
  healthScoreSource: "canonical" | "derived";
  topLanguage: string;
}

export function healthColor(score: number): string {
  if (score >= 70) return "var(--color-risk-low)";
  if (score >= 40) return "var(--color-risk-medium)";
  return "var(--color-risk-high)";
}

export function HealthRing({
  score,
  source,
  size = 36,
}: {
  score: number;
  source: "canonical" | "derived";
  size?: number;
}) {
  const r = (size - 4) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - score / 100);
  const color = healthColor(score);
  const label = source === "derived" ? "Estimated health score" : "Health score";

  return (
    <svg
      width={size}
      height={size}
      className="shrink-0"
      aria-label={`${label}: ${Math.round(score)}`}
    >
      <title>{label}</title>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="color-mix(in srgb, var(--color-border-default) 10%, transparent)"
        strokeWidth={3}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={3}
        strokeDasharray={circ}
        strokeDashoffset={offset}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text
        x={size / 2}
        y={size / 2}
        textAnchor="middle"
        dominantBaseline="central"
        fill={color}
        fontSize={10}
        fontWeight={700}
      >
        {Math.round(score)}
      </text>
    </svg>
  );
}

function WorkspaceGraphNodeInner({ data }: NodeProps) {
  const d = data as unknown as WorkspaceGraphNodeData;
  const langColor = languageColor(d.topLanguage);

  return (
    <div
      className="rounded-xl cursor-pointer transition-shadow duration-200 hover:shadow-xl"
      style={{
        width: 160,
        minHeight: 100,
        background: `linear-gradient(135deg, ${langColor}25 0%, color-mix(in srgb, var(--color-bg-surface) 95%, transparent) 60%)`,
        border: `2px solid ${langColor}60`,
        boxShadow: `0 2px 12px rgba(0,0,0,0.3), 0 0 0 1px ${langColor}20`,
        padding: "10px 12px",
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!w-2 !h-2 !bg-[var(--color-border-subtle)] !border-none"
      />

      <div className="text-[12px] font-bold text-[var(--color-text-primary)] truncate text-center mb-2">
        {d.name}
      </div>

      <div className="flex items-center gap-2.5">
        <div className="flex flex-col items-center gap-0.5">
          <HealthRing score={d.healthScore} source={d.healthScoreSource} />
          <span className="text-[8px] font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
            {d.healthScoreSource === "derived" ? "est." : "health"}
          </span>
        </div>
        <div className="flex-1 min-w-0 space-y-1">
          <div className="flex items-center gap-1 text-[10px] text-[var(--color-text-secondary)]">
            <FileText className="w-3 h-3 shrink-0" />
            <span>{d.fileCount} files</span>
          </div>
          <div className="flex items-center gap-1 text-[10px] text-[var(--color-text-secondary)]">
            <BarChart3 className="w-3 h-3 shrink-0" />
            <span>{Math.round(d.coveragePct)}% cov</span>
          </div>
        </div>
      </div>

      <div
        className="mt-2 text-[10px] font-medium px-1.5 py-0.5 rounded-full text-center"
        style={{
          background: `${langColor}30`,
          color: langColor,
        }}
      >
        {d.topLanguage}
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        className="!w-2 !h-2 !bg-[var(--color-border-subtle)] !border-none"
      />
    </div>
  );
}

export const WorkspaceGraphNode = memo(WorkspaceGraphNodeInner);
