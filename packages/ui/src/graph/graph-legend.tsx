"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { LANGUAGE_COLORS } from "../lib/confidence";
import { EDGE_COLORS, getCommunityColor } from "./sigma/constants";
import type { ColorMode, ViewMode } from "./graph-toolbar";

const LANGUAGE_LEGEND = [
  { lang: "python", color: LANGUAGE_COLORS.python, label: "Python" },
  { lang: "typescript", color: LANGUAGE_COLORS.typescript, label: "TypeScript" },
  { lang: "go", color: LANGUAGE_COLORS.go, label: "Go" },
  { lang: "rust", color: LANGUAGE_COLORS.rust, label: "Rust" },
  { lang: "java", color: LANGUAGE_COLORS.java, label: "Java" },
  { lang: "config", color: LANGUAGE_COLORS.config, label: "Config" },
  { lang: "other", color: LANGUAGE_COLORS.other, label: "Other" },
];

interface GraphLegendProps {
  nodeCount: number;
  edgeCount: number;
  colorMode: ColorMode;
  viewMode: ViewMode;
  communityLabels?: Map<number, string>;
  onCommunityClick?: (communityId: number) => void;
  activeCommunities?: Set<number> | undefined;
  onCommunityToggle?: (communityId: number) => void;
  onToggleAllCommunities?: (selectAll: boolean) => void;
  visibleEdgeTypes?: Set<string> | undefined;
  onEdgeTypeToggle?: ((edgeType: string) => void) | undefined;
}

export function GraphLegend({
  nodeCount,
  edgeCount,
  colorMode,
  viewMode,
  communityLabels,
  onCommunityClick,
  activeCommunities,
  onCommunityToggle,
  onToggleAllCommunities,
  visibleEdgeTypes,
  onEdgeTypeToggle,
}: GraphLegendProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)]/80 backdrop-blur-sm text-xs shadow-lg shadow-black/20 min-w-[120px] max-w-[150px]">
      <button
        onClick={() => setExpanded((s) => !s)}
        className="flex items-center justify-between w-full px-2.5 py-2 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
      >
        <span className="font-medium tabular-nums">
          {nodeCount} nodes &middot; {edgeCount} edges
        </span>
        {expanded ? (
          <ChevronDown className="w-3 h-3 shrink-0 ml-1.5" />
        ) : (
          <ChevronUp className="w-3 h-3 shrink-0 ml-1.5" />
        )}
      </button>

      {expanded && (
        <div className="px-2.5 pb-2.5 space-y-1.5 border-t border-[var(--color-border-default)] pt-2">
          <p className="text-[9px] text-[var(--color-text-tertiary)] uppercase tracking-wider font-medium">
            {colorMode === "language" ? "Language" : colorMode === "community" ? "Community" : "Risk"}
          </p>

          {colorMode === "language" &&
            LANGUAGE_LEGEND.map((l) => (
              <div key={l.lang} className="flex items-center gap-2 text-[var(--color-text-tertiary)]">
                <span
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: l.color }}
                />
                <span>{l.label}</span>
              </div>
            ))}

          {colorMode === "community" && (() => {
            const entries = communityLabels && communityLabels.size > 0
              ? Array.from(communityLabels.entries()).slice(0, 8)
              : null;
            const allSelected = !activeCommunities || (entries
              ? entries.every(([cid]) => activeCommunities.has(cid))
              : true);
            return (
              <>
                {onToggleAllCommunities && entries && (
                  <button
                    onClick={() => onToggleAllCommunities(!allSelected)}
                    className="text-[9px] text-[var(--color-accent-graph)] hover:underline mb-0.5"
                  >
                    {allSelected ? "Deselect All" : "Select All"}
                  </button>
                )}
                {entries
                  ? entries.map(([cid, label], i) => {
                      const color = getCommunityColor(cid);
                      const checked = !activeCommunities || activeCommunities.has(cid);
                      return (
                        <div
                          key={cid}
                          className={`flex items-center gap-2 text-[var(--color-text-tertiary)]${
                            onCommunityClick
                              ? " cursor-pointer rounded px-1 -mx-1 hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] transition-colors"
                              : ""
                          }`}
                        >
                          {onCommunityToggle && (
                            <button
                              onClick={(e) => { e.stopPropagation(); onCommunityToggle(cid); }}
                              className="shrink-0 w-[10px] h-[10px] rounded-sm border"
                              style={{
                                borderColor: color,
                                background: checked ? color : "transparent",
                              }}
                              aria-label={`Toggle community ${label}`}
                            />
                          )}
                          {!onCommunityToggle && (
                            <span
                              className="w-2 h-2 rounded-full shrink-0"
                              style={{ background: color }}
                            />
                          )}
                          <span
                            className="truncate"
                            onClick={() => onCommunityClick?.(cid)}
                          >
                            {label}
                          </span>
                        </div>
                      );
                    })
                  : Array.from({ length: 6 }, (_, i) => (
                      <div key={i} className="flex items-center gap-2 text-[var(--color-text-tertiary)]">
                        <span
                          className="w-2 h-2 rounded-full shrink-0"
                          style={{ background: getCommunityColor(i) }}
                        />
                        <span>Community {i + 1}</span>
                      </div>
                    ))}
              </>
            );
          })()}

          {colorMode === "risk" && (
            <>
              <div className="flex items-center gap-2 text-[var(--color-text-tertiary)]">
                <span className="w-2 h-2 rounded-full shrink-0 bg-[#22c55e]" />
                <span>Low risk</span>
              </div>
              <div className="flex items-center gap-2 text-[var(--color-text-tertiary)]">
                <span className="w-2 h-2 rounded-full shrink-0 bg-[#f59520]" />
                <span>Medium risk</span>
              </div>
              <div className="flex items-center gap-2 text-[var(--color-text-tertiary)]">
                <span className="w-2 h-2 rounded-full shrink-0 bg-[#ef4444]" />
                <span>High risk</span>
              </div>
            </>
          )}

          {onEdgeTypeToggle && visibleEdgeTypes && (
            <>
              <p className="text-[9px] text-[var(--color-text-tertiary)] uppercase tracking-wider font-medium pt-1.5 border-t border-[var(--color-border-default)] mt-1.5">
                Edges
              </p>
              {([
                { type: "import", label: "Imports", color: EDGE_COLORS.import },
                { type: "crossCommunity", label: "Cross-community", color: EDGE_COLORS.crossCommunity },
                { type: "internal", label: "Internal", color: EDGE_COLORS.internal },
                { type: "dynamic", label: "Dynamic", color: EDGE_COLORS.dynamic },
                { type: "lowConfidence", label: "Low confidence", color: EDGE_COLORS.lowConfidence },
              ] as const).map((et) => {
                const checked = visibleEdgeTypes.has(et.type);
                return (
                  <div key={et.type} className="flex items-center gap-2 text-[var(--color-text-tertiary)]">
                    <button
                      onClick={() => onEdgeTypeToggle(et.type)}
                      className="shrink-0 w-[10px] h-[10px] rounded-sm border"
                      style={{
                        borderColor: et.color,
                        background: checked ? et.color : "transparent",
                      }}
                      aria-label={`Toggle ${et.label} edges`}
                    />
                    <span className={checked ? "" : "line-through opacity-50"}>
                      {et.label}
                    </span>
                  </div>
                );
              })}
            </>
          )}

          {viewMode !== "module" && viewMode !== "full" && (
            <p className="text-[9px] text-[var(--color-text-tertiary)] pt-1 border-t border-[var(--color-border-default)]">
              {viewMode === "dead" && "Showing unreachable files"}
              {viewMode === "hotfiles" && "Most-committed files (30d)"}
              {viewMode === "architecture" && "Entry-point reachable (3 hops)"}
              {viewMode === "unified" && "Unified: community + risk signals"}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
