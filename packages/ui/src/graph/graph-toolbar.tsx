"use client";

import {
  Palette,
  Network,
  EyeOff,
  Maximize,
  Route,
  GitFork,
  Skull,
  Flame,
  Workflow,
  Search,
  X,
  GitBranch,
  Waypoints,
  SlidersHorizontal,
  HelpCircle,
} from "lucide-react";
import { memo, useState } from "react";
import { Button } from "../ui/button";

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
  onViewChange: (mode: ViewMode) => void;
  colorMode: ColorMode;
  onColorModeChange: (mode: ColorMode) => void;
  hideTests: boolean;
  onHideTestsChange: (v: boolean) => void;
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

// The constellation (Knowledge Graph) scope is always radial — a single
// disabled-looking indicator replaces the Force/Hierarchical toggle there.
const RADIAL_LAYOUT: { id: LayoutMode; icon: typeof GitFork; label: string } = {
  id: "radial",
  icon: GitFork,
  label: "Radial",
};

/**
 * One panel, not four.
 *
 * Scope, node filter, the layout/colour/action row and search each used to be
 * their own rounded box with its own border, its own `shadow-sm` and its own
 * `backdrop-blur-sm`, stacked down the same edge with 6px of canvas showing
 * between them. Four frames and four shadows for one control surface, over a
 * diagram the reader is trying to look past (rule 13). They are now one shell
 * with hairline dividers, sharing the legend's chrome so the two corners of
 * the canvas read as the same system.
 */
const panelClass =
  "overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/85 shadow-sm backdrop-blur-sm";

/** A divider row inside the panel. The first group omits the top hairline. */
const groupClass =
  "flex items-center gap-0.5 p-1 border-t border-[var(--color-border-default)] first:border-t-0";

const itemActiveClass =
  "bg-[var(--color-accent-primary)]/15 text-[var(--color-accent-primary)]";
const itemIdleClass =
  "text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-secondary)]";
const itemClass =
  "flex items-center gap-1.5 rounded-md px-2 py-2 text-[10px] font-medium transition-colors sm:py-1";

export const GraphToolbar = memo(function GraphToolbar({
  viewMode,
  onViewChange,
  colorMode,
  onColorModeChange,
  hideTests,
  onHideTestsChange,
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
  // Below sm the full control cluster is too much chrome over the canvas —
  // collapse it behind a single toggle, keeping search always reachable.
  const [mobileOpen, setMobileOpen] = useState(false);
  const clusterVisibility = mobileOpen ? "flex" : "hidden sm:flex";
  // Derive scope + overlays from the legacy ViewMode so this component remains
  // the single source of truth — callers can continue to round-trip the
  // wire-format ``viewMode`` value through query params without translation.
  const { scope: activeScope, overlays: activeOverlays } = viewModeToScopeOverlays(viewMode);

  // The Knowledge Graph (constellation) scope is a fixed radial composition:
  // overlays / FA2 / hierarchical layout don't apply, so those controls are
  // hidden here rather than shown in a half-working state.
  const isConstellation = activeScope === "architecture";

  // Exclusive node filter. Legacy "unified" URLs parse to both overlays and
  // render as Dead here; any click normalizes back to a single filter.
  const activeFilter: Overlay | "all" = activeOverlays.has("dead")
    ? "dead"
    : activeOverlays.has("hot")
      ? "hot"
      : "all";

  const setNodeFilter = (id: Overlay | "all") => {
    const next = new Set<Overlay>(id === "all" ? [] : [id]);
    onViewChange(scopeOverlaysToViewMode(activeScope, next));
  };

  return (
    <div className="flex flex-col gap-1.5 items-end">
      {/* Mobile: single toggle for the control cluster */}
      <button
        onClick={() => setMobileOpen((s) => !s)}
        className={`flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm px-2 py-1.5 text-[10px] font-medium shadow-sm sm:hidden ${
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

      <div className={panelClass}>
      {/* Node filter (exclusive All / Hot / Dead) — not applicable in the constellation */}
      {!isConstellation && (
      <div
        role="radiogroup"
        aria-label="Node filter"
        className={`${clusterVisibility} ${groupClass}`}
      >
        {NODE_FILTERS.map((f) => {
          const Icon = f.icon;
          const isActive = activeFilter === f.id;
          return (
            <button
              key={f.id}
              onClick={() => setNodeFilter(f.id)}
              className={`${itemClass} ${isActive ? itemActiveClass : itemIdleClass}`}
              title={f.hint}
              aria-label={f.label}
              role="radio"
              aria-checked={isActive}
            >
              {Icon && <Icon className="w-3 h-3" />}
              <span className={Icon ? "hidden lg:inline" : undefined}>{f.label}</span>
            </button>
          );
        })}
      </div>
      )}

      {/* Layout · colour · actions. */}
      <div className={`${clusterVisibility} ${groupClass}`}>
        <div className="flex gap-0.5">
          {isConstellation ? (
            // Constellation is locked to the radial layout; show a single
            // active indicator instead of the Force/Hierarchical toggle.
            <button
              key={RADIAL_LAYOUT.id}
              disabled
              className={`${itemClass} ${itemActiveClass} cursor-default`}
              title={`${RADIAL_LAYOUT.label} (fixed for Communities)`}
              aria-label={RADIAL_LAYOUT.label}
              aria-pressed
            >
              <RADIAL_LAYOUT.icon className="w-3 h-3" />
            </button>
          ) : (
            LAYOUT_MODES.map((m) => {
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
            })
          )}
        </div>

        {/* Colour-by. This control decides what every circle on the canvas
            means, and it used to be three unlabelled icons — a palette, a
            network and a shield — so there was no way to know whether you were
            looking at languages, communities or risk without hovering each
            one. Worse, Risk paints green/amber/red and Community paints
            families that include green, amber and red: same marks, two
            vocabularies, switched by a mystery glyph. The active mode now
            always carries its word. */}
        <div className="flex items-center gap-0.5 border-l border-[var(--color-border-default)] pl-1">
          <span className="hidden pl-1 pr-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] lg:inline">
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

        {/* No theme control here. It set the *global* theme, so it did exactly
            what the app's own toggle in the header does, a few hundred pixels
            away — two controls, one effect, and this one buried in a row of a
            dozen unlabelled icons. */}
        <div className="flex gap-0.5 border-l border-[var(--color-border-default)] pl-1">
          {/* Path finder / execution flows operate on file-level nodes and
              don't apply to the community constellation — hidden there. */}
          {!isConstellation && pathFinderAvailable && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onTogglePathFinder}
            className={`h-8 w-8 sm:h-7 sm:w-7 p-0 ${showPathFinder ? "text-[var(--color-accent-primary)]" : "text-[var(--color-text-tertiary)]"}`}
            title="Find dependency path"
            aria-label="Find dependency path"
            aria-pressed={showPathFinder}
          >
            <Route className="w-3.5 h-3.5" />
          </Button>
          )}
          {!isConstellation && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onToggleFlows}
            className={`h-8 w-8 sm:h-7 sm:w-7 p-0 ${showFlows ? "text-[var(--color-accent-primary)]" : "text-[var(--color-text-tertiary)]"}`}
            title="Execution flows"
            aria-label="Execution flows"
            aria-pressed={showFlows}
          >
            <Workflow className="w-3.5 h-3.5" />
          </Button>
          )}
          {!isConstellation && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onHideTestsChange(!hideTests)}
            className={`h-8 w-8 sm:h-7 sm:w-7 p-0 ${hideTests ? "text-[var(--color-accent-primary)]" : "text-[var(--color-text-tertiary)]"}`}
            title={hideTests ? "Show test files" : "Hide test files"}
            aria-label={hideTests ? "Show test files" : "Hide test files"}
            aria-pressed={hideTests}
          >
            <EyeOff className="w-3.5 h-3.5" />
          </Button>
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
          still works when the rest of the cluster is collapsed on a phone. */}
      <div className="flex items-center gap-1.5 border-t border-[var(--color-border-default)] px-2 py-1.5">
        <Search className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={onSearchKeyDown}
          placeholder="Search nodes…"
          aria-label="Search graph nodes"
          className="w-full min-w-0 sm:w-28 bg-transparent text-xs text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] lg:w-40"
        />
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
      </div>
      </div>
    </div>
  );
});
