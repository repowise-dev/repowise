"use client";

import { X, GitCommit, User, Clock, ArrowDownToLine, ArrowUpFromLine, FileText } from "lucide-react";
import { Button } from "@repowise/ui/ui/button";
import { formatRelativeTime } from "@/lib/utils/format";
import type { EgoGraphResponse } from "@/lib/api/types";

interface GraphEgoSidebarProps {
  graph: EgoGraphResponse;
  onClose: () => void;
  onNavigateToNode?: (nodeId: string) => void;
}

export function GraphEgoSidebar({ graph, onClose, onNavigateToNode }: GraphEgoSidebarProps) {
  const meta = graph.center_git_meta;
  const centerNode = graph.nodes.find((n) => n.node_id === graph.center_node_id);

  return (
    <div className="absolute top-3 right-3 z-20 w-[min(18rem,calc(100vw-1.5rem))] rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)]/95 backdrop-blur-sm shadow-xl text-xs">
      {/* Header */}
      <div className="flex items-start justify-between p-3 border-b border-[var(--color-border-default)]">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5">
            <FileText className="h-3.5 w-3.5 text-[var(--color-text-tertiary)] shrink-0" />
            <p className="font-mono text-[var(--color-text-primary)] font-medium truncate">
              {graph.center_node_id.split("/").pop()}
            </p>
          </div>
          <p className="font-mono text-[var(--color-text-tertiary)] truncate pl-5">
            {graph.center_node_id}
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose} className="h-6 w-6 p-0 shrink-0 ml-2">
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Stats */}
      <div className="p-3 space-y-2">
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-md bg-[var(--color-bg-elevated)] p-2">
            <div className="flex items-center gap-1.5 text-[var(--color-text-tertiary)] mb-1">
              <ArrowDownToLine className="h-3 w-3" />
              <span>Inbound</span>
            </div>
            <p className="text-lg font-bold text-[var(--color-text-primary)] tabular-nums">
              {graph.inbound_count}
            </p>
          </div>
          <div className="rounded-md bg-[var(--color-bg-elevated)] p-2">
            <div className="flex items-center gap-1.5 text-[var(--color-text-tertiary)] mb-1">
              <ArrowUpFromLine className="h-3 w-3" />
              <span>Outbound</span>
            </div>
            <p className="text-lg font-bold text-[var(--color-text-primary)] tabular-nums">
              {graph.outbound_count}
            </p>
          </div>
        </div>

        {centerNode && (
          <div className="flex justify-between text-[var(--color-text-secondary)] pt-1">
            <span>Symbols</span>
            <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
              {centerNode.symbol_count}
            </span>
          </div>
        )}

        {/* Git metadata */}
        {meta && (
          <>
            <div className="border-t border-[var(--color-border-default)] pt-2 space-y-1.5">
              {meta.primary_owner_name && (
                <div className="flex items-center justify-between text-[var(--color-text-secondary)]">
                  <div className="flex items-center gap-1.5">
                    <User className="h-3 w-3" />
                    <span>Owner</span>
                  </div>
                  <span className="font-medium text-[var(--color-text-primary)] truncate max-w-[120px]">
                    {meta.primary_owner_name}
                  </span>
                </div>
              )}
              {meta.last_commit_at != null && (
                <div className="flex items-center justify-between text-[var(--color-text-secondary)]">
                  <div className="flex items-center gap-1.5">
                    <Clock className="h-3 w-3" />
                    <span>Last commit</span>
                  </div>
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {formatRelativeTime(meta.last_commit_at)}
                  </span>
                </div>
              )}
              <div className="flex items-center justify-between text-[var(--color-text-secondary)]">
                <div className="flex items-center gap-1.5">
                  <GitCommit className="h-3 w-3" />
                  <span>Commits (30d)</span>
                </div>
                <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
                  {meta.commit_count_30d}
                </span>
              </div>
            </div>
          </>
        )}

        {/* Neighborhood nodes */}
        {graph.nodes.length > 1 && (
          <div className="border-t border-[var(--color-border-default)] pt-2">
            <p className="text-[var(--color-text-tertiary)] mb-1.5">
              {graph.nodes.length - 1} neighbors in {graph.links.length} edges
            </p>
            <div className="space-y-0.5 max-h-32 overflow-y-auto">
              {graph.nodes
                .filter((n) => n.node_id !== graph.center_node_id)
                .slice(0, 8)
                .map((n) => (
                  <button
                    key={n.node_id}
                    onClick={() => onNavigateToNode?.(n.node_id)}
                    className="w-full text-left px-1.5 py-1 rounded hover:bg-[var(--color-bg-elevated)] font-mono text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] truncate transition-colors"
                  >
                    {n.node_id}
                  </button>
                ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
