"use client";

import { useState, memo } from "react";
import type { ReactNode } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { LANGUAGE_COLORS } from "../lib/confidence";
import { edgeColorsForTheme } from "./sigma/constants";
import { useGraphCommunityFamilies } from "./community-colors";
import type { ColorMode, ViewMode } from "./graph-toolbar";

/** Community rows shown before the key folds into a "+N" line. */
const COMMUNITY_ROWS = 8;

/**
 * Edge kinds, in the order they are keyed.
 *
 * `dynamic` is keyed "Co-change": it is the `co_changes` edge, two files that
 * change together in git history, not a runtime dependency. Keyed "Dynamic",
 * it read as dynamic imports, which is a different claim.
 *
 * There is no "Imports" row any more. `classifyEdge` returned that kind only
 * for an edge with an endpoint missing from the graph, and those are dropped
 * before they are drawn — so the swatch keyed zero marks in every state, and
 * because it was also in the default visible set, "cross-community only" read
 * as a two-kind default.
 */
const EDGE_KINDS = [
  { type: "crossCommunity", label: "Cross-community" },
  { type: "internal", label: "Internal" },
  { type: "dynamic", label: "Co-change" },
  { type: "lowConfidence", label: "Low confidence" },
] as const;

/** The same kinds, worded for a canvas holding one community. */
const SCOPED_EDGE_KINDS = [
  { type: "internal", label: "Within this group" },
  { type: "crossCommunity", label: "Leaving this group" },
  { type: "dynamic", label: "Co-change" },
  { type: "lowConfidence", label: "Low confidence" },
] as const;

/** The overview draws cross-community edges as bands between clusters. */
const OVERVIEW_EDGE_LABELS: Partial<Record<string, string>> = {
  crossCommunity: "Between communities",
  internal: "Within a community",
};

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
  "flex w-full flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-[var(--color-border-default)] pt-2 text-xs";

const headerClass =
  "inline-flex shrink-0 items-center gap-1 rounded py-1.5 text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)] sm:py-0";

const countClass = "font-mono text-[10px] tabular-nums tracking-[0.04em]";

const rowClass =
  "inline-flex items-center gap-1.5 rounded px-1.5 py-1.5 text-left text-[11px] text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)] sm:py-0.5";

/** Section label inside the key. Mono micro-label, no rule above it. */
const groupClass =
  "shrink-0 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

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
  /** Files in no community, keyed as its own row outside the ranking. */
  constellationUnclustered?: { count: number; onClick?: (() => void) | undefined } | undefined;
  /**
   * Name of the one community being drawn, when the canvas has been drilled
   * into. Its presence selects the scoped reading of this key.
   */
  scopeLabel?: string | undefined;
  /** Members vs one-hop boundary stubs in the drawn slice. */
  sliceCounts?: { members: number; boundary: number } | undefined;
  /** Community id of the drawn slice, for the swatch hue. */
  scopeCommunityId?: number | undefined;
  /** Edges actually drawn under the current edge-type filter. Falls back to
   *  `edgeCount`, which counts hidden ones too. */
  visibleEdgeCount?: number | undefined;
  /** Rendered beside the node count, so the control that changes how many
   *  nodes are drawn sits next to the figure reporting it. */
  nodeFilter?: ReactNode | undefined;
  /** Communities actually on the canvas. `communityLabels` is the repo-wide
   *  summary list, which on a capped file graph names groups no node here
   *  belongs to; keying those offered a toggle that could not do anything. */
  drawnCommunityIds?: Set<number> | undefined;
  /**
   * The whole-repo Files overview: what it leaves out, and the direction key
   * for a focused file's edges. Its presence selects that reading.
   */
  overview?: OverviewLegend | undefined;
  /** Rendered at the row's far end in every reading (the shortcuts button). */
  trailing?: ReactNode | undefined;
}

export interface OverviewLegend {
  /** Third-party nodes not drawn (0 while they are shown). */
  hiddenExternal: number;
  /** Third-party nodes drawn, counted apart from the repo's own files. */
  shownExternal: number;
  /** Files with no dependency edge to anything drawn. */
  hiddenUnlinked: number;
  /** Community pairs whose link is below the band floor, so not drawn. */
  omittedLinks: number;
  /** Dependency edges drawn (co-change and low confidence never count). */
  dependencies: number;
  showExternal: boolean;
  onShowExternalChange: (next: boolean) => void;
}

/** Kinds the overview reveals only on a hovered or selected file. */
const FOCUS_ONLY_KINDS = new Set(["dynamic", "lowConfidence"]);

/** A short stroke, for keying a line rather than a node. */
function LineSwatch({ color, dashed }: { color: string; dashed?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-0 w-3.5 shrink-0 border-t-2"
      style={{ borderColor: color, borderStyle: dashed ? "dashed" : "solid" }}
    />
  );
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
  constellationUnclustered,
  scopeLabel,
  sliceCounts,
  scopeCommunityId,
  visibleEdgeCount,
  nodeFilter,
  drawnCommunityIds,
  overview,
  trailing,
}: GraphLegendProps) {
  // Open by default: every node is painted from this key, so a collapsed one
  // ships a field of coloured circles with no way to read them.
  const [expanded, setExpanded] = useState(true);
  const communityFamily = useGraphCommunityFamilies();
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
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {entries.length === 0 && (
              <p className="text-[11px] text-[var(--color-text-tertiary)]">
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
                  <span className="truncate max-w-[12rem]">{e.label}</span>
                  <span className="shrink-0 tabular-nums text-[10px] text-[var(--color-text-tertiary)]">
                    {e.memberCount}
                  </span>
                </button>
              );
            })}
            {overflow > 0 && (
              <p className="text-[10px] text-[var(--color-text-tertiary)]">
                +{overflow} smaller not listed
              </p>
            )}
            {constellationUnclustered && constellationUnclustered.count > 0 && (
              <button
                type="button"
                onClick={constellationUnclustered.onClick}
                className={rowClass}
                title="Files with no dependency on the rest of the repo"
              >
                <Swatch color="var(--color-text-tertiary)" />
                <span>Not grouped</span>
                <span className="shrink-0 tabular-nums text-[10px] text-[var(--color-text-tertiary)]">
                  {constellationUnclustered.count}
                </span>
              </button>
            )}
          </div>
        )}
        {trailing && <span className="ml-auto">{trailing}</span>}
      </div>
    );
  }

  const drawnEdgeCount = visibleEdgeCount ?? edgeCount;

  // Communities worth a row: the repo-wide summary, narrowed to what is on the
  // canvas. `communityLabels` resolves before an async graph build finishes, so
  // `drawnCommunityIds` is legitimately empty for those frames — and a key with
  // no rows must not still offer a control that dims everything.
  const keyedCommunities =
    communityLabels && communityLabels.size > 0
      ? Array.from(communityLabels.entries()).filter(
          ([cid]) => !drawnCommunityIds || drawnCommunityIds.has(cid),
        )
      : null;
  const hasCommunityRows = keyedCommunities === null || keyedCommunities.length > 0;

  // Inside a community the whole-repo key describes the wrong thing. Its eight
  // community swatches come from the repo-wide summary list in API order, so
  // the community you drilled into is usually not even among them; its
  // Deselect-all dims every node on the canvas including the ones you came to
  // read; and toggling a community that is not drawn changes nothing while
  // leaving its swatch filled. Community filtering is a whole-repo grammar, so
  // the scoped key drops it and keys what is on the canvas — following
  // `health/map/legend.tsx` and `system-map-legend.tsx`, where the key reads
  // the same source the marks do.
  if (scopeLabel) {
    const members = sliceCounts?.members ?? nodeCount;
    const boundary = sliceCounts?.boundary ?? 0;
    return (
      <div className={shellClass}>
        <button onClick={() => setExpanded((s) => !s)} className={headerClass}>
          <span className={countClass}>
            {members} file{members === 1 ? "" : "s"}
            {boundary > 0 ? ` · ${boundary} outside` : ""} &middot;{" "}
            {drawnEdgeCount} edge{drawnEdgeCount === 1 ? "" : "s"} shown
          </span>
          <Chevron expanded={expanded} />
        </button>
        {expanded && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <div className={rowClass}>
              <Swatch
                color={
                  colorMode === "community" && scopeCommunityId !== undefined
                    ? communityFamily(scopeCommunityId).satellite
                    : undefined
                }
              />
              <span className="max-w-[14rem] truncate">{scopeLabel}</span>
              <span className="shrink-0 tabular-nums text-[10px] text-[var(--color-text-tertiary)]">
                {members}
              </span>
            </div>
            {boundary > 0 && (
              <div className={rowClass}>
                {/* The faded ring was explained in prose and keyed nowhere. */}
                <span className="h-2 w-2 shrink-0 rounded-full border border-[var(--color-text-tertiary)]" />
                <span>Outside this group</span>
                <span className="shrink-0 tabular-nums text-[10px] text-[var(--color-text-tertiary)]">
                  {boundary}
                </span>
              </div>
            )}
            {onEdgeTypeToggle && visibleEdgeTypes && (
              <>
                <p className={groupClass}>Edges</p>
                {SCOPED_EDGE_KINDS.map((et) => {
                  const checked = visibleEdgeTypes.has(et.type);
                  return (
                    <div key={et.type} className={rowClass}>
                      <SwatchToggle
                        color={edgeColors[et.type]}
                        checked={checked}
                        label={`Toggle ${et.label} edges`}
                        onToggle={() => onEdgeTypeToggle(et.type)}
                      />
                      <span className={`truncate ${checked ? "" : "opacity-45"}`}>
                        {et.label}
                      </span>
                    </div>
                  );
                })}
              </>
            )}
          </div>
        )}
        {trailing && <span className="ml-auto">{trailing}</span>}
      </div>
    );
  }

  return (
    <div className={shellClass}>
      <button
        onClick={() => setExpanded((s) => !s)}
        className={headerClass}
        aria-expanded={expanded}
      >
        <span className={countClass}>
          {overview
            ? [
                `${(nodeCount - overview.shownExternal).toLocaleString()} files`,
                overview.shownExternal > 0
                  ? `${overview.shownExternal.toLocaleString()} third-party`
                  : null,
                `${overview.dependencies.toLocaleString()} dependencies`,
              ]
                .filter(Boolean)
                .join(" · ")
            : `${nodeCount} nodes · ${drawnEdgeCount} edges`}
        </span>
        <Chevron expanded={expanded} />
      </button>
      {nodeFilter}
      {overview && (
        // What the picture leaves out, beside the count it changes. Rule:
        // state what is not drawn.
        <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--color-text-secondary)]">
          {(overview.hiddenExternal > 0 || overview.showExternal) && (
            <span className="inline-flex items-center gap-1.5">
              {overview.showExternal
                ? "Third-party modules shown"
                : `${overview.hiddenExternal.toLocaleString()} third-party modules hidden`}
              <button
                type="button"
                onClick={() => overview.onShowExternalChange(!overview.showExternal)}
                aria-pressed={overview.showExternal}
                aria-label={
                  overview.showExternal ? "Hide third-party modules" : "Show third-party modules"
                }
                className="rounded px-1 py-0.5 font-medium text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
              >
                {overview.showExternal ? "Hide" : "Show"}
              </button>
            </span>
          )}
          {overview.hiddenUnlinked > 0 && (
            <span title="Files with no dependency edge to anything drawn have no place in the layout and nothing to reveal on hover.">
              {overview.hiddenUnlinked.toLocaleString()} unlinked files not drawn
            </span>
          )}
          {overview.omittedLinks > 0 && (
            <span title="Pairs of communities with too few dependencies between them to band. Their edges still show on a hovered or selected file.">
              {overview.omittedLinks.toLocaleString()} weaker community links not drawn
            </span>
          )}
        </span>
      )}

      {expanded && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {(colorMode === "language" || hasCommunityRows) && (
            <p className={groupClass}>
              {colorMode === "language" ? "Language" : "Community"}
            </p>
          )}

          {colorMode === "language" &&
            LANGUAGE_LEGEND.map((l) => (
              <div
                key={l.lang}
                className="inline-flex items-center gap-1.5 text-[11px] text-[var(--color-text-secondary)]"
              >
                <Swatch color={l.color} />
                <span className="truncate">{l.label}</span>
              </div>
            ))}

          {colorMode === "community" && hasCommunityRows && (() => {
            const all = keyedCommunities;
            const entries = all ? all.slice(0, COMMUNITY_ROWS) : null;
            const overflow = all ? all.length - entries!.length : 0;
            // Over every row, not the eight shown: the label read "Deselect
            // all" while communities nine and beyond were already off.
            const allSelected = !activeCommunities || (all
              ? all.every(([cid]) => activeCommunities.has(cid))
              : true);
            return (
              <>
                {onToggleAllCommunities && entries && (
                  <button
                    onClick={() => onToggleAllCommunities(!allSelected)}
                    className="shrink-0 text-[10px] font-medium text-[var(--color-accent-primary)] hover:underline"
                  >
                    {allSelected ? "Deselect all" : "Select all"}
                  </button>
                )}
                {entries
                  ? entries.map(([cid, label]) => {
                      // Satellite, not hub: file nodes are painted
                      // `family.satellite` by use-sigma's colour pass, so the
                      // hub hue keyed a mark this canvas never draws.
                      const color = communityFamily(cid).satellite;
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
                          {onCommunityClick ? (
                            <button
                              type="button"
                              onClick={() => onCommunityClick(cid)}
                              className="max-w-[12rem] truncate rounded hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                            >
                              {label}
                            </button>
                          ) : (
                            <span className="max-w-[12rem] truncate">{label}</span>
                          )}
                        </div>
                      );
                    })
                  : Array.from({ length: 6 }, (_, i) => (
                      <div
                        key={i}
                        className="inline-flex items-center gap-1.5 text-[11px] text-[var(--color-text-secondary)]"
                      >
                        <Swatch color={communityFamily(i).hub} />
                        <span className="truncate">Community {i + 1}</span>
                      </div>
                    ))}
                {overflow > 0 && (
                  <p className="text-[10px] text-[var(--color-text-tertiary)]">
                    +{overflow} smaller not listed
                  </p>
                )}
              </>
            );
          })()}

          {onEdgeTypeToggle && visibleEdgeTypes && (() => {
            const toggle = (et: (typeof EDGE_KINDS)[number]) => {
              const checked = visibleEdgeTypes.has(et.type);
              const label = (overview && OVERVIEW_EDGE_LABELS[et.type]) || et.label;
              return (
                <div key={et.type} className={rowClass}>
                  <SwatchToggle
                    // The overview draws these in community hues (bands,
                    // cluster texture) or in the direction hues on a focused
                    // file, never in the kind colours, so it keys none of them.
                    color={overview ? "var(--color-text-secondary)" : edgeColors[et.type]}
                    checked={checked}
                    label={`Toggle ${label} edges`}
                    onToggle={() => onEdgeTypeToggle(et.type)}
                  />
                  <span className={`truncate ${checked ? "" : "opacity-45"}`}>
                    {label}
                  </span>
                </div>
              );
            };
            if (!overview) {
              return (
                <>
                  <p className={groupClass}>Edges</p>
                  {EDGE_KINDS.map(toggle)}
                </>
              );
            }
            // The overview draws co-change and low-confidence edges only on a
            // focused file, so their toggles sit under that heading rather
            // than beside kinds that change the picture at rest.
            return (
              <>
                <p className={groupClass}>Edges</p>
                {EDGE_KINDS.filter((et) => !FOCUS_ONLY_KINDS.has(et.type)).map(toggle)}
                {/* A focused file's edges carry direction, in two hues that
                    must be named: colour alone cannot say which way a
                    dependency points. */}
                <p className={groupClass}>Hover or select a file</p>
                <span className="inline-flex items-center gap-1.5 text-[11px] text-[var(--color-text-secondary)]">
                  <LineSwatch color="var(--color-accent-primary)" />
                  imports
                </span>
                <span className="inline-flex items-center gap-1.5 text-[11px] text-[var(--color-text-secondary)]">
                  <LineSwatch color="var(--color-accent-secondary)" />
                  imported by
                </span>
                {EDGE_KINDS.filter((et) => FOCUS_ONLY_KINDS.has(et.type)).map(toggle)}
              </>
            );
          })()}

          {viewMode !== "full" && (
            <p className="text-[10px] text-[var(--color-text-tertiary)]">
              {viewMode === "dead" && "Showing unreachable files"}
              {viewMode === "hotfiles" && "Most-committed files (30d)"}
              {viewMode === "unified" && "Dead and high-churn files"}
            </p>
          )}
        </div>
      )}
      {trailing && <span className="ml-auto">{trailing}</span>}
    </div>
  );
});
