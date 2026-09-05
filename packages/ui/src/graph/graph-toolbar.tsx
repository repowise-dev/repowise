"use client";

import {
  Palette,
  Network,
  Maximize,
  Route,
  Skull,
  Flame,
  Workflow,
  Search,
  X,
  GitBranch,
  Waypoints,
  SlidersHorizontal,
  HelpCircle,
  ChevronDown,
} from "lucide-react";
import { memo, useState } from "react";
import { Button } from "../ui/button";
import { Popover, PopoverTrigger, PopoverContent } from "../ui/popover";

/**
 * No "risk" member. There was one, and it painted `pagerank * 3` through
 * green/amber/red thresholds of 0.3 and 0.7 (`sigma/use-sigma.ts`). PageRank is
 * a probability distribution summing to 1 across every node, so on any repo
 * above roughly ten files nothing can reach 0.233 — the highest value in this
 * codebase's own index is 0.036, which is 0.108 after the ×3. The lens was
 * green by construction, on every repo, always.
 *
 * It was also spending the green/amber/red band vocabulary that rule 2
 * reserves for real health readouts, on centrality — so a reader who learned
 * those colours from Code Health was being taught the opposite thing here.
 *
 * The product does have a real defect-risk score, but `graph_nodes` carries no
 * health column, so an honest lens needs the payload to gain one first. Until
 * then this ships two lenses that both encode something true.
 */
export type ColorMode = "language" | "community";
export type ViewMode = "full" | "architecture" | "dead" | "hotfiles" | "unified";
export type LayoutMode = "hierarchical" | "force" | "radial";
export type GraphTheme = "light" | "dark";

/**
 * Orthogonal model:
 *   Scope ("which subset of nodes do we render?")
 *     × Overlays ("which signals do we highlight on top?")
 *
 * The legacy ViewMode is preserved as the wire/state format so existing
 * callers and query-param routing keep working. The helpers below convert
 * freely in both directions.
 *
 * There is no "modules" scope. It drew one circle per top-level directory,
 * and on this repo `packages/` held 69% of the files — a list that skewed is a
 * bad canvas. It is now a module *filter* over the file graph (see
 * `use-module-filter`), which cost a scope, an endpoint, a breadcrumb trail,
 * drill-down state and expand-on-double-click, and gained a control that
 * partitions the repo instead of pretending to.
 */
export type Scope = "architecture" | "full";
export type Overlay = "dead" | "hot";

export function scopeOverlaysToViewMode(scope: Scope, overlays: ReadonlySet<Overlay>): ViewMode {
  const hasDead = overlays.has("dead");
  const hasHot = overlays.has("hot");
  if (hasDead && hasHot) return "unified";
  if (hasDead) return "dead";
  if (hasHot) return "hotfiles";
  return scope; // "architecture" | "full"
}

export function viewModeToScopeOverlays(view: ViewMode): { scope: Scope; overlays: Set<Overlay> } {
  switch (view) {
    case "architecture":
      return { scope: "architecture", overlays: new Set() };
    case "dead":
      return { scope: "full", overlays: new Set(["dead"]) };
    case "hotfiles":
      return { scope: "full", overlays: new Set(["hot"]) };
    case "unified":
      return { scope: "full", overlays: new Set(["dead", "hot"]) };
    case "full":
    default:
      return { scope: "full", overlays: new Set() };
  }
}

interface GraphToolbarProps {
  viewMode: ViewMode;
  /**
   * @deprecated The All/Hot/Dead filter moved to {@link GraphNodeFilter}, which
   * renders beside the node count it changes. Accepted and ignored here so an
   * out-of-tree host compiles while it ports.
   */
  onViewChange?: ((mode: ViewMode) => void) | undefined;
  colorMode: ColorMode;
  onColorModeChange: (mode: ColorMode) => void;
  /**
   * @deprecated Removed. The control was titled "Hide test files" and filtered
   * *search results* only — `activeSignals` never reached the renderer, so
   * every test file stayed drawn. Accepted and ignored rather than shipping a
   * button that does not do what it says.
   */
  hideTests?: boolean | undefined;
  /** @deprecated See {@link GraphToolbarProps.hideTests}. */
  onHideTestsChange?: ((v: boolean) => void) | undefined;
  onFitView: () => void;
  showPathFinder: boolean;
  onTogglePathFinder: () => void;
  /** Hosts without a path-finder implementation hide the toggle entirely. */
  pathFinderAvailable?: boolean;
  showFlows: boolean;
  onToggleFlows: () => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  searchMatchCount?: number;
  searchTotalCount?: number;
  onSearchKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  layoutMode: LayoutMode;
  onLayoutModeChange: (mode: LayoutMode) => void;
  /** Opens the keyboard-shortcut help overlay (also bound to `?`). */
  onToggleHelp?: () => void;
  /** Why the hierarchical layout cannot run on this graph, if it cannot.
   *  Renders the toggle disabled with the reason as its tooltip instead of
   *  letting it look live and then refuse on click — ELK's 500-node cap sits
   *  BELOW the graph loader's 1,500-node floor, so on any repo bigger than
   *  that the button was unreachable by construction and said so only after
   *  you pressed it. */
  hierarchicalDisabledReason?: string | undefined;
}

// No scope cluster here. Scope is one axis and it now has one control, in the
// section header (`GraphScopeSwitcher`), following the Code Health precedent —
// floating it over the diagram while the page tabs steered the same axis is
// what made "Communities" appear twice on one screen.

// Node filter = exclusive All / Hot / Dead segmented control. Hot and dead
// files are near-disjoint sets, so the old pair of independent toggles read
// as an AND filter and mostly produced an empty view when both were lit; a
// single exclusive control matches how the affordance is read.
const NODE_FILTERS: { id: Overlay | "all"; icon?: typeof Skull; label: string; hint: string }[] = [
  { id: "all", label: "All", hint: "Show every node" },
  { id: "hot", icon: Flame, label: "Hot", hint: "High-churn files" },
  { id: "dead", icon: Skull, label: "Dead", hint: "Dead-code files" },
];

const COLOR_MODES: { id: ColorMode; icon: typeof Palette; label: string }[] = [
  { id: "language", icon: Palette, label: "Language" },
  { id: "community", icon: Network, label: "Community" },
];

const LAYOUT_MODES: { id: LayoutMode; icon: typeof GitBranch; label: string }[] = [
  { id: "force", icon: Waypoints, label: "Force (FA2)" },
  { id: "hierarchical", icon: GitBranch, label: "Hierarchical" },
];

/**
 * Groups inside the one header cluster.
 *
 * A hairline *between* groups, not a box *around* them. This was a bordered
 * panel whose three groups stacked as blocks, so beside the scope controls it
 * read as a second toolbar. Every peer canvas puts exactly one cluster in the
 * header band: `health/triage-view` (lens switcher only), the Knowledge Graph
 * page (one hairline row), `workspace/system-map` (filters only) and
 * `coupling/coupling-explorer` (hairline rows, no box). DESIGN_LANGUAGE.md
 * reserves bordered containers for objects that can be selected, opened or
 * acted on; a cluster of loose toggles is not one.
 */
const groupClass = "flex items-center gap-0.5";

const dividerClass =
  "ml-0.5 border-l border-[var(--color-border-default)] pl-1.5";

const itemActiveClass =
  "bg-[var(--color-accent-primary)]/15 text-[var(--color-accent-primary)]";
const itemIdleClass =
  "text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-secondary)]";
const itemClass =
  "flex items-center gap-1.5 rounded-md px-2 py-2 text-[10px] font-medium transition-colors sm:py-1";

/** Segmented control, matching `coupling-explorer`'s: counts live inside the
 *  segments rather than in a caption beside them. */
const segmentGroupClass =
  "inline-flex overflow-hidden rounded-md border border-[var(--color-border-default)]";
const segmentClass =
  "shrink-0 whitespace-nowrap border-r border-[var(--color-border-default)] px-2 py-1.5 text-[10px] font-medium transition-colors last:border-r-0 sm:py-1";

/**
 * Exclusive All / Hot / Dead node filter, rendered beside the node count it
 * changes rather than in the header.
 *
 * A control whose entire effect is "how many nodes are drawn" belongs next to
 * the figure reporting that number — DESIGN_LANGUAGE.md's rule that a control
 * must change the same dataset its caption counts.
 *
 * No totals inside the segments. They would be repo-wide, sitting a few pixels
 * from a node count that is view-scoped, with nothing saying the two figures
 * count different sets. `OverlayCountChip` already reconciles them as
 * "380 of 412" in the canvas status row, where there is room to say so.
 */
export function GraphNodeFilter({
  viewMode,
  onViewChange,
  className,
}: {
  viewMode: ViewMode;
  onViewChange: (mode: ViewMode) => void;
  className?: string | undefined;
}) {
  const { scope, overlays } = viewModeToScopeOverlays(viewMode);
  const active: Overlay | "all" = overlays.has("dead")
    ? "dead"
    : overlays.has("hot")
      ? "hot"
      : "all";

  return (
    <div
      role="radiogroup"
      aria-label="Node filter"
      className={`${segmentGroupClass} ${className ?? ""}`}
    >
      {NODE_FILTERS.map((f) => {
        const Icon = f.icon;
        const isActive = active === f.id;
        return (
          <button
            key={f.id}
            type="button"
            role="radio"
            aria-checked={isActive}
            title={f.hint}
            onClick={() =>
              onViewChange(
                scopeOverlaysToViewMode(
                  scope,
                  new Set<Overlay>(f.id === "all" ? [] : [f.id]),
                ),
              )
            }
            className={`${segmentClass} ${
              isActive
                ? "bg-[var(--color-accent-primary)]/15 text-[var(--color-accent-primary)]"
                : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)]"
            }`}
          >
            <span className="inline-flex items-center gap-1">
              {Icon && <Icon className="h-3 w-3" />}
              {f.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export const GraphToolbar = memo(function GraphToolbar({
  viewMode,
  colorMode,
  onColorModeChange,
  onFitView,
  showPathFinder,
  onTogglePathFinder,
  pathFinderAvailable = true,
  showFlows,
  onToggleFlows,
  searchQuery,
  onSearchChange,
  searchMatchCount,
  searchTotalCount,
  onSearchKeyDown,
  layoutMode,
  onLayoutModeChange,
  onToggleHelp,
  hierarchicalDisabledReason,
}: GraphToolbarProps) {
  // Below sm the full control cluster is too much chrome — collapse it behind
  // a single toggle, keeping search always reachable.
  const [mobileOpen, setMobileOpen] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const clusterVisibility = mobileOpen ? "flex" : "hidden sm:flex";
  // Derive scope from the legacy ViewMode so this component remains the single
  // source of truth — callers can continue to round-trip the wire-format
  // `viewMode` value through query params without translation.
  const { scope: activeScope } = viewModeToScopeOverlays(viewMode);

  // The Knowledge Graph (constellation) scope is a fixed radial composition:
  // overlays / FA2 / hierarchical layout don't apply, so those controls are
  // hidden here rather than shown in a half-working state.
  const isConstellation = activeScope === "architecture";

  const traceActive = showPathFinder || showFlows;

  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      <div className={`${clusterVisibility} flex-wrap items-center gap-y-1`}>
        {!isConstellation && (
          <div className={groupClass}>
            {LAYOUT_MODES.map((m) => {
              const Icon = m.icon;
              const isActive = layoutMode === m.id;
              const disabledReason =
                m.id === "hierarchical" ? hierarchicalDisabledReason : undefined;
              return (
                <button
                  key={m.id}
                  onClick={() => onLayoutModeChange(m.id)}
                  disabled={!!disabledReason}
                  className={`${itemClass} ${isActive ? itemActiveClass : itemIdleClass} ${
                    disabledReason ? "cursor-not-allowed opacity-40" : ""
                  }`}
                  title={disabledReason ?? m.label}
                  aria-label={m.label}
                  aria-disabled={!!disabledReason}
                  aria-pressed={isActive}
                >
                  <Icon className="w-3 h-3" />
                </button>
              );
            })}
          </div>
        )}

        {/* Colour-by. The active mode always carries its word: unlabelled
            glyphs gave no way to tell which vocabulary the circles were in.
            Hidden in the constellation, where hubs are family-coloured
            regardless of this setting. */}
        {!isConstellation && (
          <div className={`${groupClass} ${dividerClass}`}>
            <span className="hidden pr-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] lg:inline">
              Colour
            </span>
            {COLOR_MODES.map((m) => {
              const Icon = m.icon;
              const isActive = colorMode === m.id;
              return (
                <button
                  key={m.id}
                  onClick={() => onColorModeChange(m.id)}
                  className={`${itemClass} ${isActive ? itemActiveClass : itemIdleClass}`}
                  title={m.label}
                  aria-label={m.label}
                  aria-pressed={isActive}
                >
                  <Icon className="w-3 h-3" />
                  {isActive && <span>{m.label}</span>}
                </button>
              );
            })}
          </div>
        )}

        {/* No theme control here. It set the *global* theme, so it did exactly
            what the app's own toggle in the header does, a few hundred pixels
            away — two controls, one effect, and this one buried in a row of a
            dozen unlabelled icons.

            Path finding and execution flows are two uncommon actions that were
            two unlabelled glyphs. DESIGN_LANGUAGE.md: do not abbreviate an
            uncommon action to make it fit. One named control, opened on
            demand, carries both with their words. */}
        <div className={`${groupClass} ${isConstellation ? "" : dividerClass}`}>
          {!isConstellation && (
            <Popover open={traceOpen} onOpenChange={setTraceOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className={`${itemClass} ${traceActive ? itemActiveClass : itemIdleClass}`}
                  title={
                    pathFinderAvailable
                      ? "Trace a dependency path or an execution flow"
                      : "Trace an execution flow"
                  }
                  // Not colour alone. The two buttons this replaced each
                  // carried their own `aria-pressed`; without this a reader who
                  // cannot see the accent has to open the menu to learn a trace
                  // panel is already open.
                  aria-label={
                    showPathFinder
                      ? "Trace — dependency path open"
                      : showFlows
                        ? "Trace — execution flows open"
                        : "Trace"
                  }
                >
                  <Route className="w-3 h-3" />
                  <span>Trace</span>
                  <ChevronDown className="w-3 h-3" />
                </button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-56 p-1">
                {pathFinderAvailable && (
                  <button
                    type="button"
                    onClick={() => {
                      setTraceOpen(false);
                      onTogglePathFinder();
                    }}
                    aria-pressed={showPathFinder}
                    className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-bg-wash-hover)] ${
                      showPathFinder
                        ? "text-[var(--color-accent-primary)]"
                        : "text-[var(--color-text-primary)]"
                    }`}
                  >
                    <Route className="h-3.5 w-3.5 shrink-0" />
                    Find dependency path
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setTraceOpen(false);
                    onToggleFlows();
                  }}
                  aria-pressed={showFlows}
                  className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-bg-wash-hover)] ${
                    showFlows
                      ? "text-[var(--color-accent-primary)]"
                      : "text-[var(--color-text-primary)]"
                  }`}
                >
                  <Workflow className="h-3.5 w-3.5 shrink-0" />
                  Execution flows
                </button>
              </PopoverContent>
            </Popover>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={onFitView}
            className="h-8 w-8 sm:h-7 sm:w-7 p-0 text-[var(--color-text-tertiary)]"
            title="Fit view"
            aria-label="Fit view"
          >
            <Maximize className="w-3.5 h-3.5" />
          </Button>
          {onToggleHelp && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onToggleHelp}
              className="h-8 w-8 sm:h-7 sm:w-7 p-0 text-[var(--color-text-tertiary)]"
              title="Keyboard shortcuts (?)"
              aria-label="Keyboard shortcuts"
            >
              <HelpCircle className="w-3.5 h-3.5" />
            </Button>
          )}
        </div>
      </div>

      {/* Search stays visible at every width — it is the one control that
          still works when the rest of the cluster is collapsed on a phone. A
          bordered field of its own width, rather than a row of the old panel,
          so it stops stretching to whatever the widest stacked group needed. */}
      <div className="relative">
        <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={onSearchKeyDown}
          placeholder="Search nodes…"
          aria-label="Search graph nodes"
          className="w-40 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] py-1.5 pl-7 pr-14 text-xs text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] focus:border-[var(--color-border-hover)] sm:w-36 lg:w-48"
        />
        <span className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1">
          {searchQuery && searchMatchCount != null && searchTotalCount != null && (
            <span className="whitespace-nowrap font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
              {searchMatchCount}/{searchTotalCount}
            </span>
          )}
          {searchQuery && (
            <button
              onClick={() => onSearchChange("")}
              aria-label="Clear search"
              className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </span>
      </div>

      {/* Mobile: single toggle for the control cluster */}
      <button
        onClick={() => setMobileOpen((s) => !s)}
        className={`flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2 py-1.5 text-[10px] font-medium sm:hidden ${
          mobileOpen
            ? "text-[var(--color-accent-primary)]"
            : "text-[var(--color-text-secondary)]"
        }`}
        aria-expanded={mobileOpen}
        aria-label="Graph controls"
      >
        <SlidersHorizontal className="w-3 h-3" />
        Controls
      </button>
    </div>
  );
});
