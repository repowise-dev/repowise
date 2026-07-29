"use client";

import { useState, memo } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { LANGUAGE_COLORS } from "../lib/confidence";
import { edgeColorsForTheme } from "./sigma/constants";
import { useCommunityFamilies } from "../shared/use-theme-tokens";
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

/**
 * Legend chrome, shared by the constellation and the file/module readings.
 *
 * Sleeker than what it replaced: one hairline instead of three (the header
 * rule, the section rule and the per-block rules all did the same job), a
 * single 6px gutter instead of nested 10px padding, and rows that are flush
 * hit targets rather than text with negative margins hung off it. The old box
 * spent about a third of its height on borders and uppercase section labels
 * describing two or three entries each — rule 3's "if a section's label is a
 * large fraction of its content, it wants merging".
 */
const shellClass =
  "overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/85 text-xs shadow-sm backdrop-blur-sm";

const headerClass =
  "flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)]";

const countClass = "font-mono text-[10px] tabular-nums tracking-[0.04em]";

const rowClass =
  "flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-[11px] text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)]";

/** Section label inside the key. Mono micro-label, no rule above it. */
const groupClass =
  "px-1.5 pt-1.5 pb-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

function Chevron({ expanded }: { expanded: boolean }) {
  const Icon = expanded ? ChevronDown : ChevronUp;
  return <Icon className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />;
}

// `color` is optional because LANGUAGE_COLORS is an index signature — an
// unknown language resolves to undefined and the swatch just renders empty.
function Swatch({ color }: { color: string | undefined }) {
  return (
    <span
      className="h-2 w-2 shrink-0 rounded-full"
      style={{ background: color }}
    />
  );
}

/** A toggleable swatch. Same 8px mark as `Swatch` so a filterable row and a
 *  static one line up on the same optical grid; unchecked reads as an outline
 *  rather than a different shape. */
function SwatchToggle({
  color,
  checked,
  label,
  onToggle,
}: {
  color: string;
  checked: boolean;
  label: string;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      className="h-4 w-4 shrink-0 rounded-full border sm:h-2 sm:w-2"
      style={{ borderColor: color, background: checked ? color : "transparent" }}
      aria-label={label}
      aria-pressed={checked}
    />
  );
}

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
  graphTheme?: "light" | "dark" | undefined;
  /** Constellation (Knowledge Graph) rows: family swatch + label + member count. */
  constellationEntries?:
    | { communityId: number; label: string; memberCount: number }[]
    | undefined;
  /** Click a constellation row → focus that hub's camera. */
  onConstellationHubClick?: ((communityId: number) => void) | undefined;
}

export const GraphLegend = memo(function GraphLegend({
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
  graphTheme = "dark",
  constellationEntries,
  onConstellationHubClick,
}: GraphLegendProps) {
  // Open by default. Every node on the canvas is painted from this key, so
  // collapsing it ships a field of coloured circles with no way to read them
  // until the reader thinks to click a counter. The key is the cheapest thing
  // on screen and the only one that makes the rest mean anything.
  const [expanded, setExpanded] = useState(true);
  const communityFamily = useCommunityFamilies();
  const edgeColors = edgeColorsForTheme(graphTheme);
  const isConstellation = viewMode === "architecture";

  // Constellation legend: families + member counts, click focuses the hub.
  if (isConstellation) {
    const allEntries = constellationEntries ?? [];
    const entries = allEntries.slice(0, 12);
    const overflow = allEntries.length - entries.length;
    return (
      <div className={shellClass}>
        <button onClick={() => setExpanded((s) => !s)} className={headerClass}>
          <span className={countClass}>
            {allEntries.length} communit{allEntries.length === 1 ? "y" : "ies"}
          </span>
          <Chevron expanded={expanded} />
        </button>
        {expanded && (
          <div className="space-y-px px-1.5 pb-1.5">
            {entries.length === 0 && (
              <p className="px-1.5 py-1 text-[11px] text-[var(--color-text-tertiary)]">
                No communities detected
              </p>
            )}
            {entries.map((e) => {
              const color = communityFamily(e.communityId).hub;
              return (
                <button
                  key={e.communityId}
                  onClick={() => onConstellationHubClick?.(e.communityId)}
                  className={rowClass}
                >
                  <Swatch color={color} />
                  <span className="min-w-0 flex-1 truncate">{e.label}</span>
                  <span className="shrink-0 tabular-nums text-[10px] text-[var(--color-text-tertiary)]">
                    {e.memberCount}
                  </span>
                </button>
              );
            })}
            {overflow > 0 && (
              <p className="px-1.5 pt-1 text-[10px] text-[var(--color-text-tertiary)]">
                +{overflow} smaller not listed
              </p>
            )}
            <p className="px-1.5 pt-1.5 text-[10px] text-[var(--color-text-tertiary)]">
              Inner ring = entry surface
            </p>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={`${shellClass} min-w-[148px] max-w-[190px]`}>
      <button onClick={() => setExpanded((s) => !s)} className={headerClass}>
        <span className={countClass}>
          {nodeCount} nodes &middot; {edgeCount} edges
        </span>
        <Chevron expanded={expanded} />
      </button>

      {expanded && (
        <div className="space-y-px px-1.5 pb-1.5">
          <p className={groupClass}>
            {colorMode === "language" ? "Language" : "Community"}
          </p>

          {colorMode === "language" &&
            LANGUAGE_LEGEND.map((l) => (
              <div
                key={l.lang}
                className="flex items-center gap-2 px-1.5 py-0.5 text-[11px] text-[var(--color-text-secondary)]"
              >
                <Swatch color={l.color} />
                <span className="truncate">{l.label}</span>
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
                    className="px-1.5 pb-0.5 text-[10px] font-medium text-[var(--color-accent-primary)] hover:underline"
                  >
                    {allSelected ? "Deselect all" : "Select all"}
                  </button>
                )}
                {entries
                  ? entries.map(([cid, label]) => {
                      const color = communityFamily(cid).hub;
                      const checked = !activeCommunities || activeCommunities.has(cid);
                      return (
                        <div key={cid} className={rowClass}>
                          {onCommunityToggle ? (
                            <SwatchToggle
                              color={color}
                              checked={checked}
                              label={`Toggle community ${label}`}
                              onToggle={() => onCommunityToggle(cid)}
                            />
                          ) : (
                            <Swatch color={color} />
                          )}
                          <span
                            className="min-w-0 flex-1 truncate"
                            onClick={() => onCommunityClick?.(cid)}
                          >
                            {label}
                          </span>
                        </div>
                      );
                    })
                  : Array.from({ length: 6 }, (_, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-2 px-1.5 py-0.5 text-[11px] text-[var(--color-text-secondary)]"
                      >
                        <Swatch color={communityFamily(i).hub} />
                        <span className="truncate">Community {i + 1}</span>
                      </div>
                    ))}
              </>
            );
          })()}

          {onEdgeTypeToggle && visibleEdgeTypes && (
            <>
              <p className={groupClass}>Edges</p>
              {([
                { type: "import", label: "Imports", color: edgeColors.import },
                { type: "crossCommunity", label: "Cross-community", color: edgeColors.crossCommunity },
                { type: "internal", label: "Internal", color: edgeColors.internal },
                { type: "dynamic", label: "Dynamic", color: edgeColors.dynamic },
                { type: "lowConfidence", label: "Low confidence", color: edgeColors.lowConfidence },
              ] as const).map((et) => {
                const checked = visibleEdgeTypes.has(et.type);
                return (
                  <div key={et.type} className={rowClass}>
                    <SwatchToggle
                      color={et.color}
                      checked={checked}
                      label={`Toggle ${et.label} edges`}
                      onToggle={() => onEdgeTypeToggle(et.type)}
                    />
                    <span
                      className={`min-w-0 flex-1 truncate ${checked ? "" : "opacity-45"}`}
                    >
                      {et.label}
                    </span>
                  </div>
                );
              })}
            </>
          )}

          {viewMode !== "full" && (
            <p className="px-1.5 pt-1.5 text-[10px] text-[var(--color-text-tertiary)]">
              {viewMode === "dead" && "Showing unreachable files"}
              {viewMode === "hotfiles" && "Most-committed files (30d)"}
              {viewMode === "unified" && "Unified: community + risk signals"}
            </p>
          )}
        </div>
      )}
    </div>
  );
});
