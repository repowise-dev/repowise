"use client";

import { useMemo } from "react";
import {
  X,
  FileText,
  Zap,
  FlaskConical,
  BookOpen,
  ArrowDownToLine,
  ArrowUpFromLine,
  Route,
  Network,
  Code2,
} from "lucide-react";
import { ScrollArea } from "../ui/scroll-area";
import { languageColor } from "../lib/confidence";
import { formatNumber } from "../lib/format";
import type { FileNodeData } from "./elk-layout";
import type { Edge } from "@xyflow/react";

const COMMUNITY_COLORS = [
  "#6366f1", "#ec4899", "#10b981", "#f59e0b", "#3b82f6", "#a855f7",
  "#14b8a6", "#f97316", "#84cc16", "#06b6d4", "#e11d48", "#8b5cf6",
  "#22c55e", "#eab308", "#0ea5e9", "#d946ef", "#ef4444", "#78716c",
  "#64748b", "#0891b2", "#059669", "#b45309", "#7c3aed", "#db2777",
];

interface NeighborInfo {
  id: string;
  label: string;
  communityId: number;
  direction: "importer" | "import";
}

export interface GraphInspectionPanelProps {
  nodeId: string;
  data: FileNodeData;
  edges: Edge[];
  allNodes: Map<string, FileNodeData>;
  allPageranks: number[];
  allBetweenness: number[];
  communityLabel?: string | undefined;
  onClose: () => void;
  onNavigateToNode: (nodeId: string) => void;
  onViewDocs?: () => void;
  onViewSymbols?: () => void;
  onFindPath?: () => void;
  onShowEgoGraph?: () => void;
}

function percentileOf(value: number, sorted: number[]): number {
  if (sorted.length === 0) return 0;
  let count = 0;
  for (const v of sorted) {
    if (v < value) count++;
    else break;
  }
  return Math.round((count / sorted.length) * 100);
}

export function GraphInspectionPanel({
  nodeId,
  data,
  edges,
  allNodes,
  allPageranks,
  allBetweenness,
  communityLabel,
  onClose,
  onNavigateToNode,
  onViewDocs,
  onViewSymbols,
  onFindPath,
  onShowEgoGraph,
}: GraphInspectionPanelProps) {
  const neighbors = useMemo(() => {
    const result: NeighborInfo[] = [];
    const seen = new Set<string>();
    for (const edge of edges) {
      if (edge.source === nodeId && !seen.has(edge.target)) {
        seen.add(edge.target);
        const nd = allNodes.get(edge.target);
        result.push({
          id: edge.target,
          label: edge.target.split("/").pop() ?? edge.target,
          communityId: nd?.communityId ?? 0,
          direction: "import",
        });
      } else if (edge.target === nodeId && !seen.has(edge.source)) {
        seen.add(edge.source);
        const nd = allNodes.get(edge.source);
        result.push({
          id: edge.source,
          label: edge.source.split("/").pop() ?? edge.source,
          communityId: nd?.communityId ?? 0,
          direction: "importer",
        });
      }
    }
    return result;
  }, [nodeId, edges, allNodes]);

  const inDegree = neighbors.filter((n) => n.direction === "importer").length;
  const outDegree = neighbors.filter((n) => n.direction === "import").length;
  const pagerankPct = percentileOf(data.pagerank, allPageranks);
  const betweennessPct = percentileOf(data.betweenness, allBetweenness);

  return (
    <div
      className="absolute right-0 top-0 bottom-0 w-full sm:w-[300px] border-l border-[var(--color-border-default)] bg-[var(--color-bg-surface)] z-20 flex flex-col shadow-xl shadow-black/20 animate-in slide-in-from-right duration-200"
    >
      {/* Header */}
      <div className="flex items-start gap-2 px-4 py-3 border-b border-[var(--color-border-default)]">
        <FileText className="w-4 h-4 text-[var(--color-text-secondary)] mt-0.5 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
            {nodeId.split("/").pop()}
          </p>
          <p className="text-[10px] text-[var(--color-text-tertiary)] truncate mt-0.5" title={data.fullPath}>
            {data.fullPath}
          </p>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-[var(--color-bg-elevated)] transition-colors shrink-0"
        >
          <X className="h-4 w-4 text-[var(--color-text-tertiary)]" />
        </button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          {/* Metadata */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-[var(--color-text-tertiary)]">Language</span>
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full" style={{ background: languageColor(data.language) }} />
                <span className="font-medium text-[var(--color-text-primary)] capitalize">{data.language}</span>
              </span>
            </div>

            <div className="flex items-center justify-between text-xs">
              <span className="text-[var(--color-text-tertiary)]">Symbols</span>
              <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                {formatNumber(data.symbolCount)}
              </span>
            </div>

            <div className="flex items-center justify-between text-xs">
              <span className="text-[var(--color-text-tertiary)]">Pagerank</span>
              <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                Top {100 - pagerankPct}%
              </span>
            </div>

            <div className="flex items-center justify-between text-xs">
              <span className="text-[var(--color-text-tertiary)]">Betweenness</span>
              <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                Top {100 - betweennessPct}%
              </span>
            </div>

            <div className="flex items-center justify-between text-xs">
              <span className="text-[var(--color-text-tertiary)]">Community</span>
              <span className="flex items-center gap-1.5">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ background: COMMUNITY_COLORS[data.communityId % COMMUNITY_COLORS.length] }}
                />
                <span className="font-medium text-[var(--color-text-primary)]">
                  {communityLabel ?? `#${data.communityId}`}
                </span>
              </span>
            </div>

            <div className="flex items-center justify-between text-xs">
              <span className="text-[var(--color-text-tertiary)]">Degree</span>
              <span className="font-medium text-[var(--color-text-primary)] tabular-nums flex items-center gap-2">
                <span className="flex items-center gap-0.5" title="In-degree (importers)">
                  <ArrowDownToLine className="w-3 h-3 text-[var(--color-text-tertiary)]" />{inDegree}
                </span>
                <span className="flex items-center gap-0.5" title="Out-degree (imports)">
                  <ArrowUpFromLine className="w-3 h-3 text-[var(--color-text-tertiary)]" />{outDegree}
                </span>
              </span>
            </div>

            {/* Badges */}
            <div className="flex items-center gap-1.5 pt-1 flex-wrap">
              {data.isEntryPoint && (
                <span className="inline-flex items-center gap-1 rounded-md bg-[var(--color-accent-graph)]/10 text-[var(--color-accent-graph)] px-1.5 py-0.5 text-[10px] font-medium">
                  <Zap className="w-2.5 h-2.5" /> Entry Point
                </span>
              )}
              {data.isTest && (
                <span className="inline-flex items-center gap-1 rounded-md bg-purple-500/10 text-purple-400 px-1.5 py-0.5 text-[10px] font-medium">
                  <FlaskConical className="w-2.5 h-2.5" /> Test
                </span>
              )}
              {data.hasDoc ? (
                <span className="inline-flex items-center gap-1 rounded-md bg-green-500/10 text-green-400 px-1.5 py-0.5 text-[10px] font-medium">
                  <BookOpen className="w-2.5 h-2.5" /> Documented
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-md bg-slate-500/10 text-slate-400 px-1.5 py-0.5 text-[10px] font-medium">
                  <BookOpen className="w-2.5 h-2.5" /> No docs
                </span>
              )}
            </div>
          </div>

          {/* Neighbors */}
          {neighbors.length > 0 && (
            <div className="border-t border-[var(--color-border-default)] pt-3">
              <p className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
                Neighbors ({neighbors.length})
              </p>
              <div className="space-y-0.5 max-h-48 overflow-y-auto">
                {neighbors.map((n) => (
                  <button
                    key={n.id}
                    onClick={() => onNavigateToNode(n.id)}
                    className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-[var(--color-bg-elevated)] transition-colors group"
                  >
                    <span
                      className="w-0.5 h-5 rounded-full shrink-0"
                      style={{ background: COMMUNITY_COLORS[n.communityId % COMMUNITY_COLORS.length] }}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] font-mono text-[var(--color-text-secondary)] group-hover:text-[var(--color-text-primary)] truncate">
                        {n.label}
                      </p>
                    </div>
                    <span className="text-[9px] text-[var(--color-text-tertiary)] shrink-0">
                      {n.direction === "importer" ? "imports this" : "imported"}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Actions */}
      <div className="border-t border-[var(--color-border-default)] p-3 grid grid-cols-2 gap-2">
        {onViewDocs && (
          <button
            onClick={onViewDocs}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-bg-inset)] hover:bg-[var(--color-bg-surface)] border border-[var(--color-border-default)] px-2 py-1.5 text-[10px] font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <BookOpen className="w-3 h-3" /> View Docs
          </button>
        )}
        {onViewSymbols && (
          <button
            onClick={onViewSymbols}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-bg-inset)] hover:bg-[var(--color-bg-surface)] border border-[var(--color-border-default)] px-2 py-1.5 text-[10px] font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <Code2 className="w-3 h-3" /> Symbols
          </button>
        )}
        {onFindPath && (
          <button
            onClick={onFindPath}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-bg-inset)] hover:bg-[var(--color-bg-surface)] border border-[var(--color-border-default)] px-2 py-1.5 text-[10px] font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <Route className="w-3 h-3" /> Find Path
          </button>
        )}
        {onShowEgoGraph && (
          <button
            onClick={onShowEgoGraph}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-bg-inset)] hover:bg-[var(--color-bg-surface)] border border-[var(--color-border-default)] px-2 py-1.5 text-[10px] font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <Network className="w-3 h-3" /> Ego Graph
          </button>
        )}
      </div>
    </div>
  );
}
