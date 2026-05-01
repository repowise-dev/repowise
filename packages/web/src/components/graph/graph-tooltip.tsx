"use client";

import { useEffect, useRef } from "react";
import { FileText, Folder, ArrowRight, X, Zap, FlaskConical, BookOpen } from "lucide-react";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { languageColor } from "@repowise-dev/ui/lib/confidence";
import type { FileNodeData, ModuleNodeData } from "./elk-layout";

interface GraphTooltipProps {
  nodeId: string;
  nodeType: string;
  data: Record<string, unknown>;
  x: number;
  y: number;
  onClose: () => void;
  onViewDocs: () => void;
  onExplore?: () => void;
}

function importanceLabel(pagerank: number): { label: string; color: string } {
  if (pagerank >= 0.01) return { label: "High", color: "#ef4444" };
  if (pagerank >= 0.003) return { label: "Medium", color: "#f59520" };
  return { label: "Low", color: "#22c55e" };
}

export function GraphTooltip({
  nodeId,
  nodeType,
  data,
  x,
  y,
  onClose,
  onViewDocs,
  onExplore,
}: GraphTooltipProps) {
  const ref = useRef<HTMLDivElement>(null);

  // Dismiss on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  // Smart positioning: prefer below-right, flip if near edges
  const tooltipW = 260;
  const tooltipH = 280;
  const pad = 12;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1200;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;

  let left = x + pad;
  let top = y + pad;
  if (left + tooltipW > vw - pad) left = x - tooltipW - pad;
  if (top + tooltipH > vh - pad) top = y - tooltipH - pad;
  if (left < pad) left = pad;
  if (top < pad) top = pad;

  const isFile = nodeType === "fileNode";
  const isModule = nodeType === "moduleGroup";

  return (
    <div
      ref={ref}
      className="fixed z-50 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-2xl shadow-black/30 backdrop-blur-md text-xs animate-in fade-in slide-in-from-bottom-1 duration-150"
      style={{ left, top, width: tooltipW }}
    >
      {/* Header */}
      <div className="flex items-start gap-2 p-3 pb-2 border-b border-[var(--color-border-default)]/50">
        <div className="mt-0.5 shrink-0">
          {isModule ? (
            <Folder className="w-4 h-4 text-[var(--color-accent-graph)]" />
          ) : (
            <FileText className="w-4 h-4 text-[var(--color-text-secondary)]" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-mono font-semibold text-[var(--color-text-primary)] text-[11px] leading-tight break-all">
            {nodeId.split("/").pop()}
          </p>
          <p className="text-[10px] text-[var(--color-text-tertiary)] mt-0.5 truncate">
            {nodeId}
          </p>
        </div>
        <button
          onClick={onClose}
          className="shrink-0 p-0.5 rounded hover:bg-[var(--color-bg-inset)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] transition-colors"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Details */}
      <div className="p-3 space-y-2">
        {isFile && (() => {
          const d = data as unknown as FileNodeData;
          const imp = importanceLabel(d.pagerank);
          return (
            <>
              {/* Language */}
              <div className="flex items-center justify-between">
                <span className="text-[var(--color-text-tertiary)]">Language</span>
                <span className="flex items-center gap-1.5">
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{ background: languageColor(d.language) }}
                  />
                  <span className="font-medium text-[var(--color-text-primary)] capitalize">
                    {d.language}
                  </span>
                </span>
              </div>

              {/* Symbols */}
              <div className="flex items-center justify-between">
                <span className="text-[var(--color-text-tertiary)]">Symbols</span>
                <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                  {formatNumber(d.symbolCount)}
                </span>
              </div>

              {/* Importance */}
              <div className="flex items-center justify-between">
                <span className="text-[var(--color-text-tertiary)]">Importance</span>
                <span className="font-medium tabular-nums" style={{ color: imp.color }}>
                  {imp.label}
                </span>
              </div>

              {/* Community */}
              <div className="flex items-center justify-between">
                <span className="text-[var(--color-text-tertiary)]">Community</span>
                <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                  #{d.communityId}
                </span>
              </div>

              {/* Betweenness centrality */}
              <div className="flex items-center justify-between">
                <span className="text-[var(--color-text-tertiary)]">Betweenness</span>
                <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                  {d.betweenness < 0.001 ? "<0.1%" : `${(d.betweenness * 100).toFixed(1)}%`}
                </span>
              </div>

              {/* Dead code view: confidence group */}
              {typeof d.confidenceGroup === "string" && (
                <div className="flex items-center justify-between">
                  <span className="text-[var(--color-text-tertiary)]">Confidence</span>
                  <span className="font-medium text-[var(--color-text-primary)] capitalize">
                    {(d.confidenceGroup as string)}
                  </span>
                </div>
              )}

              {/* Hot files view: commit count */}
              {typeof d.commitCount === "number" && (
                <div className="flex items-center justify-between">
                  <span className="text-[var(--color-text-tertiary)]">Commits (30d)</span>
                  <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                    {formatNumber(d.commitCount as number)}
                  </span>
                </div>
              )}

              {/* Badges row */}
              <div className="flex items-center gap-1.5 pt-1 flex-wrap">
                {d.isEntryPoint && (
                  <span className="inline-flex items-center gap-1 rounded-md bg-[var(--color-accent-graph)]/10 text-[var(--color-accent-graph)] px-1.5 py-0.5 text-[10px] font-medium">
                    <Zap className="w-2.5 h-2.5" /> Entry Point
                  </span>
                )}
                {d.isTest && (
                  <span className="inline-flex items-center gap-1 rounded-md bg-purple-500/10 text-purple-400 px-1.5 py-0.5 text-[10px] font-medium">
                    <FlaskConical className="w-2.5 h-2.5" /> Test
                  </span>
                )}
                {d.hasDoc ? (
                  <span className="inline-flex items-center gap-1 rounded-md bg-green-500/10 text-green-400 px-1.5 py-0.5 text-[10px] font-medium">
                    <BookOpen className="w-2.5 h-2.5" /> Documented
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 rounded-md bg-slate-500/10 text-slate-400 px-1.5 py-0.5 text-[10px] font-medium">
                    <BookOpen className="w-2.5 h-2.5" /> No docs
                  </span>
                )}
              </div>
            </>
          );
        })()}

        {isModule && (() => {
          const d = data as unknown as ModuleNodeData;
          const docPct = d.docCoveragePct ?? 0;
          return (
            <>
              <div className="flex items-center justify-between">
                <span className="text-[var(--color-text-tertiary)]">Files</span>
                <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                  {formatNumber(d.fileCount ?? 0)}
                </span>
              </div>
              {d.symbolCount != null && d.symbolCount > 0 && (
                <div className="flex items-center justify-between">
                  <span className="text-[var(--color-text-tertiary)]">Symbols</span>
                  <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                    {formatNumber(d.symbolCount)}
                  </span>
                </div>
              )}
              <div className="flex items-center justify-between">
                <span className="text-[var(--color-text-tertiary)]">Doc coverage</span>
                <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                  {Math.round(docPct * 100)}%
                </span>
              </div>
              {/* Mini doc coverage bar */}
              <div className="h-1.5 w-full rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-300"
                  style={{
                    width: `${Math.max(2, Math.round(docPct * 100))}%`,
                    background: docPct >= 0.7 ? "#22c55e" : docPct >= 0.3 ? "#f59520" : "#ef4444",
                  }}
                />
              </div>
            </>
          );
        })()}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 p-3 pt-0">
        <button
          onClick={onViewDocs}
          className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-bg-inset)] hover:bg-[var(--color-bg-surface)] border border-[var(--color-border-default)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
        >
          <BookOpen className="w-3 h-3" />
          View Docs
        </button>
        {onExplore && (
          <button
            onClick={onExplore}
            className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-accent-graph)]/10 hover:bg-[var(--color-accent-graph)]/20 border border-[var(--color-accent-graph)]/30 px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-accent-graph)] transition-colors"
          >
            <ArrowRight className="w-3 h-3" />
            Explore
          </button>
        )}
      </div>
    </div>
  );
}
