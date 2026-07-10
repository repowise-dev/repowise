"use client";

import { useMemo, useRef, memo } from "react";
import {
  X,
  FileText,
  Folder,
  Zap,
  FlaskConical,
  BookOpen,
  ArrowDownToLine,
  ArrowUpFromLine,
  Route,
  Network,
  Code2,
  Flame,
  Skull,
  Lightbulb,
} from "lucide-react";
import { ScrollArea } from "../ui/scroll-area";
import { languageColor } from "../lib/confidence";
import { formatNumber } from "../lib/format";
import type { FileNodeData, ModuleNodeData } from "./elk-layout";
import type Graph from "graphology";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./sigma/types";
import { useCommunityFamilies } from "../shared/use-theme-tokens";

interface NeighborInfo {
  id: string;
  label: string;
  communityId: number;
  direction: "importer" | "import";
  /** Aggregated import count on the connecting edge (modules roll up many). */
  edgeCount: number;
}

export interface GraphInspectionPanelProps {
  nodeId: string;
  data: FileNodeData | ModuleNodeData;
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  allNodes: Map<string, FileNodeData>;
  communityLabel?: string | undefined;
  onClose: () => void;
  onNavigateToNode: (nodeId: string) => void;
  onViewDocs?: () => void;
  onViewSymbols?: (() => void) | undefined;
  /** Canonical file-page href — renders the primary "Open file page" action. */
  filePageHref?: string | undefined;
  onFindPath?: (() => void) | undefined;
  onShowEgoGraph?: (() => void) | undefined;
  onExpandModule?: (() => void) | undefined;
  /** Whether the module is currently expanded (the action becomes Collapse). */
  isModuleExpanded?: boolean | undefined;
  egoDepth?: number | undefined;
  onEgoDepthChange?: ((depth: number) => void) | undefined;
  egoVisibleCount?: number | undefined;
}

function isModuleData(data: FileNodeData | ModuleNodeData): data is ModuleNodeData {
  return data.nodeType === "module";
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

export const GraphInspectionPanel = memo(function GraphInspectionPanel({
  nodeId,
  data,
  graph,
  allNodes,
  communityLabel,
  onClose,
  onNavigateToNode,
  onViewDocs,
  onViewSymbols,
  filePageHref,
  onFindPath,
  onShowEgoGraph,
  onExpandModule,
  isModuleExpanded,
  egoDepth,
  onEgoDepthChange,
  egoVisibleCount,
}: GraphInspectionPanelProps) {
  const communityFamily = useCommunityFamilies();
  const touchStartY = useRef<number | null>(null);
  const neighbors = useMemo(() => {
    if (!graph || !graph.hasNode(nodeId)) return [];
    const result: NeighborInfo[] = [];
    const seen = new Set<string>();
    graph.forEachOutEdge(nodeId, (_edge, attrs, _source, target) => {
      if (seen.has(target)) return;
      seen.add(target);
      const nd = allNodes.get(target);
      result.push({
        id: target,
        label: target.split("/").pop() ?? target,
        communityId: nd?.communityId ?? 0,
        direction: "import",
        edgeCount: attrs.edgeCount ?? 1,
      });
    });
    graph.forEachInEdge(nodeId, (_edge, attrs, source) => {
      if (seen.has(source)) return;
      seen.add(source);
      const nd = allNodes.get(source);
      result.push({
        id: source,
        label: source.split("/").pop() ?? source,
        communityId: nd?.communityId ?? 0,
        direction: "importer",
        edgeCount: attrs.edgeCount ?? 1,
      });
    });
    return result;
  }, [nodeId, graph, allNodes]);

  const { sortedPageranks, sortedBetweenness } = useMemo(() => {
    const prs: number[] = [];
    const bts: number[] = [];
    allNodes.forEach((nd) => {
      prs.push(nd.pagerank);
      bts.push(nd.betweenness);
    });
    prs.sort((a, b) => a - b);
    bts.sort((a, b) => a - b);
    return { sortedPageranks: prs, sortedBetweenness: bts };
  }, [allNodes]);

  const isMod = isModuleData(data);
  const inDegree = neighbors.filter((n) => n.direction === "importer").length;
  const outDegree = neighbors.filter((n) => n.direction === "import").length;
  const pagerankPct = !isMod ? percentileOf((data as FileNodeData).pagerank, sortedPageranks) : 0;
  const betweennessPct = !isMod ? percentileOf((data as FileNodeData).betweenness, sortedBetweenness) : 0;

  const headerIcon = isMod
    ? <Folder className="w-4 h-4 text-[var(--color-text-secondary)] mt-0.5 shrink-0" />
    : <FileText className="w-4 h-4 text-[var(--color-text-secondary)] mt-0.5 shrink-0" />;

  return (
    <div
      // Right panel on sm+; bottom sheet (drag handle + swipe-dismiss) below.
      className="absolute inset-x-0 bottom-0 top-auto max-h-[70%] rounded-t-xl border-t border-[var(--color-border-default)] bg-[var(--color-bg-surface)] z-20 flex flex-col shadow-xl shadow-black/20 animate-in slide-in-from-bottom sm:slide-in-from-bottom-0 sm:slide-in-from-right duration-200 sm:inset-x-auto sm:right-0 sm:top-0 sm:bottom-0 sm:left-auto sm:w-[300px] sm:max-h-none sm:rounded-none sm:border-t-0 sm:border-l"
      onTouchStart={(e) => {
        touchStartY.current = e.touches[0]?.clientY ?? null;
      }}
      onTouchEnd={(e) => {
        const start = touchStartY.current;
        touchStartY.current = null;
        if (start == null || window.innerWidth >= 640) return;
        const end = e.changedTouches[0]?.clientY ?? start;
        if (end - start > 80) onClose();
      }}
    >
      {/* Drag handle (mobile sheet only) */}
      <div className="flex justify-center py-1.5 sm:hidden" aria-hidden>
        <span className="h-1 w-9 rounded-full bg-[var(--color-border-default)]" />
      </div>
      {/* Header */}
      <div className="flex items-start gap-2 px-4 py-3 border-b border-[var(--color-border-default)]">
        {headerIcon}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
            {nodeId.split("/").pop()}
          </p>
          <p className="text-caption text-[var(--color-text-tertiary)] truncate mt-0.5" title={data.fullPath}>
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
          {isMod ? (
            <ModuleMetadata data={data as ModuleNodeData} inDegree={inDegree} outDegree={outDegree} />
          ) : (
            <FileMetadata
              data={data as FileNodeData}
              pagerankPct={pagerankPct}
              betweennessPct={betweennessPct}
              inDegree={inDegree}
              outDegree={outDegree}
              communityLabel={communityLabel}
              communityColor={communityFamily((data as FileNodeData).communityId).hub}
            />
          )}

          {/* Neighbors */}
          {neighbors.length > 0 && (
            <div className="border-t border-[var(--color-border-default)] pt-3">
              <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
                {isMod ? "Connected Modules" : "Neighbors"} ({neighbors.length})
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
                      style={{ background: communityFamily(n.communityId).hub }}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-mono text-[var(--color-text-secondary)] group-hover:text-[var(--color-text-primary)] truncate">
                        {n.label}
                      </p>
                    </div>
                    <span className="text-caption text-[var(--color-text-tertiary)] shrink-0">
                      {n.direction === "importer" ? "imports this" : "imported"}
                      {n.edgeCount > 1 && (
                        <span className="tabular-nums"> · {n.edgeCount}</span>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>

      {onEgoDepthChange && (
        <div className="px-4 py-3 border-t border-[var(--color-border-default)]">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-caption font-medium text-[var(--color-text-secondary)]">
              Ego Graph
            </span>
            {egoDepth != null && egoDepth > 0 && egoVisibleCount != null && (
              <span className="text-caption text-[var(--color-text-tertiary)] tabular-nums">
                {egoVisibleCount} nodes
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-caption text-[var(--color-text-tertiary)] w-4 text-right tabular-nums">
              {egoDepth ?? 0}
            </span>
            <input
              type="range"
              min={0}
              max={5}
              value={egoDepth ?? 0}
              onChange={(e) => onEgoDepthChange(parseInt(e.target.value, 10))}
              className="flex-1 h-1 accent-[var(--color-accent-graph)] cursor-pointer"
              aria-label="Ego graph depth"
            />
            <span className="text-caption text-[var(--color-text-tertiary)]">5</span>
          </div>
          <p className="text-caption text-[var(--color-text-tertiary)] mt-1">
            {(egoDepth ?? 0) === 0
              ? "Slide to filter by hop distance"
              : `Showing nodes within ${egoDepth} hop${egoDepth === 1 ? "" : "s"}`}
          </p>
        </div>
      )}

      {/* Actions */}
      <div className="border-t border-[var(--color-border-default)] p-3 grid grid-cols-2 gap-2">
        {!isMod && filePageHref && (
          <a
            href={filePageHref}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-accent-graph)]/10 hover:bg-[var(--color-accent-graph)]/20 border border-[var(--color-accent-graph)]/30 px-2 py-1.5 text-caption font-medium text-[var(--color-accent-graph)] transition-colors col-span-2"
          >
            <FileText className="w-3 h-3" /> Open file page
          </a>
        )}
        {isMod && onExpandModule && (
          <button
            onClick={onExpandModule}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-accent-graph)]/10 hover:bg-[var(--color-accent-graph)]/20 border border-[var(--color-accent-graph)]/30 px-2 py-1.5 text-caption font-medium text-[var(--color-accent-graph)] transition-colors col-span-2"
          >
            <Network className="w-3 h-3" />
            {isModuleExpanded ? "Collapse Module" : "Expand Module"}
          </button>
        )}
        {onViewDocs && (
          <button
            onClick={onViewDocs}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-bg-inset)] hover:bg-[var(--color-bg-surface)] border border-[var(--color-border-default)] px-2 py-1.5 text-caption font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <BookOpen className="w-3 h-3" /> View Docs
          </button>
        )}
        {!isMod && onViewSymbols && (
          <button
            onClick={onViewSymbols}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-bg-inset)] hover:bg-[var(--color-bg-surface)] border border-[var(--color-border-default)] px-2 py-1.5 text-caption font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <Code2 className="w-3 h-3" /> Symbols
          </button>
        )}
        {onFindPath && (
          <button
            onClick={onFindPath}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-bg-inset)] hover:bg-[var(--color-bg-surface)] border border-[var(--color-border-default)] px-2 py-1.5 text-caption font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <Route className="w-3 h-3" /> Find Path
          </button>
        )}
        {!isMod && onShowEgoGraph && (
          <button
            onClick={onShowEgoGraph}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-bg-inset)] hover:bg-[var(--color-bg-surface)] border border-[var(--color-border-default)] px-2 py-1.5 text-caption font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <Network className="w-3 h-3" /> Ego Graph
          </button>
        )}
      </div>
    </div>
  );
});

function FileMetadata({
  data,
  pagerankPct,
  betweennessPct,
  inDegree,
  outDegree,
  communityLabel,
  communityColor,
}: {
  data: FileNodeData;
  pagerankPct: number;
  betweennessPct: number;
  inDegree: number;
  outDegree: number;
  communityLabel?: string | undefined;
  communityColor: string;
}) {
  return (
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
            style={{ background: communityColor }}
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

      <div className="flex items-center gap-1.5 pt-1 flex-wrap">
        {data.isEntryPoint && (
          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--color-accent-graph)]/10 text-[var(--color-accent-graph)] px-1.5 py-0.5 text-caption font-medium">
            <Zap className="w-2.5 h-2.5" /> Entry Point
          </span>
        )}
        {data.isTest && (
          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--color-accent-secondary)]/10 text-[var(--color-accent-secondary)] px-1.5 py-0.5 text-caption font-medium">
            <FlaskConical className="w-2.5 h-2.5" /> Test
          </span>
        )}
        {data.hasDoc ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--color-success)]/10 text-[var(--color-success)] px-1.5 py-0.5 text-caption font-medium">
            <BookOpen className="w-2.5 h-2.5" /> Documented
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-md bg-[var(--color-bg-inset)] text-[var(--color-text-tertiary)] px-1.5 py-0.5 text-caption font-medium">
            <BookOpen className="w-2.5 h-2.5" /> No docs
          </span>
        )}
      </div>
    </div>
  );
}

function ModuleMetadata({
  data,
  inDegree,
  outDegree,
}: {
  data: ModuleNodeData;
  inDegree: number;
  outDegree: number;
}) {
  const docPct = Math.round((data.docCoveragePct ?? 0) * 100);
  const docColor = docPct >= 70 ? "var(--color-success)" : docPct >= 30 ? "var(--color-warning)" : "var(--color-error)";

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--color-text-tertiary)]">Files</span>
        <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
          {formatNumber(data.fileCount)}
        </span>
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--color-text-tertiary)]">Symbols</span>
        <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
          {formatNumber(data.symbolCount)}
        </span>
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--color-text-tertiary)]">Avg Pagerank</span>
        <span className="font-medium text-[var(--color-text-primary)] tabular-nums">
          {data.avgPagerank.toFixed(4)}
        </span>
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--color-text-tertiary)]">Doc Coverage</span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full" style={{ background: docColor }} />
          <span className="font-medium text-[var(--color-text-primary)] tabular-nums">{docPct}%</span>
        </span>
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--color-text-tertiary)]">Connections</span>
        <span className="font-medium text-[var(--color-text-primary)] tabular-nums flex items-center gap-2">
          <span className="flex items-center gap-0.5" title="Depends on this">
            <ArrowDownToLine className="w-3 h-3 text-[var(--color-text-tertiary)]" />{inDegree}
          </span>
          <span className="flex items-center gap-0.5" title="This depends on">
            <ArrowUpFromLine className="w-3 h-3 text-[var(--color-text-tertiary)]" />{outDegree}
          </span>
        </span>
      </div>

      {data.primaryOwner && (
        <div className="flex items-center justify-between text-xs">
          <span className="text-[var(--color-text-tertiary)]">Owner</span>
          <span className="font-medium text-[var(--color-text-primary)] truncate max-w-[60%]" title={data.primaryOwner}>
            {data.primaryOwner}
          </span>
        </div>
      )}

      {/* Health badges — only rendered when there is something to say. */}
      {((data.hotspotCount ?? 0) > 0 || (data.deadCount ?? 0) > 0 || data.hasDecision) && (
        <div className="flex items-center gap-1.5 pt-1 flex-wrap">
          {(data.hotspotCount ?? 0) > 0 && (
            <span className="inline-flex items-center gap-1 rounded-md bg-[var(--color-warning)]/10 text-[var(--color-warning)] px-1.5 py-0.5 text-caption font-medium">
              <Flame className="w-2.5 h-2.5" /> {data.hotspotCount} hotspot{data.hotspotCount === 1 ? "" : "s"}
            </span>
          )}
          {(data.deadCount ?? 0) > 0 && (
            <span className="inline-flex items-center gap-1 rounded-md bg-[var(--color-error)]/10 text-[var(--color-error)] px-1.5 py-0.5 text-caption font-medium">
              <Skull className="w-2.5 h-2.5" /> {data.deadCount} dead
            </span>
          )}
          {data.hasDecision && (
            <span className="inline-flex items-center gap-1 rounded-md bg-[var(--color-accent-secondary)]/10 text-[var(--color-accent-secondary)] px-1.5 py-0.5 text-caption font-medium">
              <Lightbulb className="w-2.5 h-2.5" /> Decision
            </span>
          )}
        </div>
      )}
    </div>
  );
}
