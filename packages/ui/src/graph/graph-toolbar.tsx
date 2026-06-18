"use client";

import {
  Palette,
  Network,
  Shield,
  EyeOff,
  Maximize,
  Route,
  Boxes,
  GitFork,
  Skull,
  Flame,
  LayoutGrid,
  Workflow,
  Search,
  X,
  GitBranch,
  Waypoints,
  Sun,
  Moon,
  SlidersHorizontal,
  HelpCircle,
} from "lucide-react";
import { memo, useState } from "react";
import { Button } from "../ui/button";

export type ColorMode = "language" | "community" | "risk";
export type ViewMode = "module" | "full" | "architecture" | "dead" | "hotfiles" | "unified";
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
 */
export type Scope = "architecture" | "modules" | "full";
export type Overlay = "dead" | "hot";

export function scopeOverlaysToViewMode(scope: Scope, overlays: ReadonlySet<Overlay>): ViewMode {
  const hasDead = overlays.has("dead");
  const hasHot = overlays.has("hot");
  if (hasDead && hasHot) return "unified";
  if (hasDead) return "dead";
  if (hasHot) return "hotfiles";
  if (scope === "modules") return "module";
  return scope; // "architecture" | "full"
}

export function viewModeToScopeOverlays(view: ViewMode): { scope: Scope; overlays: Set<Overlay> } {
  switch (view) {
    case "module":
      return { scope: "modules", overlays: new Set() };
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
  showFlows: boolean;
  onToggleFlows: () => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  searchMatchCount?: number;
  searchTotalCount?: number;
  onSearchKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  layoutMode: LayoutMode;
  onLayoutModeChange: (mode: LayoutMode) => void;
  graphTheme: GraphTheme;
  onGraphThemeChange: (theme: GraphTheme) => void;
  /** Opens the keyboard-shortcut help overlay (also bound to `?`). */
  onToggleHelp?: () => void;
  /** Which scopes the scope cluster offers. Defaults to all three; the Explore
   *  surface omits the constellation scope (it lives in the Knowledge Graph
   *  view) so there is no cross-view jump back through the toolbar. */
  availableScopes?: Scope[] | undefined;
}

// Scope = which subset of nodes are drawn. Mutually exclusive.
const SCOPES: { id: Scope; icon: typeof Boxes; label: string; hint: string }[] = [
  { id: "architecture", icon: GitFork, label: "Communities", hint: "Detected communities" },
  { id: "modules", icon: Boxes, label: "Modules", hint: "Folder / package rollup" },
  { id: "full", icon: LayoutGrid, label: "Full", hint: "All files and symbols" },
];

// Overlays = additive signal highlights that compose with any scope.
const OVERLAYS: { id: Overlay; icon: typeof Skull; label: string }[] = [
  { id: "dead", icon: Skull, label: "Dead" },
  { id: "hot", icon: Flame, label: "Hot" },
];

const COLOR_MODES: { id: ColorMode; icon: typeof Palette; label: string }[] = [
  { id: "language", icon: Palette, label: "Language" },
  { id: "community", icon: Network, label: "Community" },
  { id: "risk", icon: Shield, label: "Risk" },
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
  showFlows,
  onToggleFlows,
  searchQuery,
  onSearchChange,
  searchMatchCount,
  searchTotalCount,
  onSearchKeyDown,
  layoutMode,
  onLayoutModeChange,
  graphTheme,
  onGraphThemeChange,
  onToggleHelp,
  availableScopes,
}: GraphToolbarProps) {
  const scopes = availableScopes
    ? SCOPES.filter((s) => availableScopes.includes(s.id))
    : SCOPES;
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

  const setScope = (next: Scope) => {
    onViewChange(scopeOverlaysToViewMode(next, activeOverlays));
  };

  const toggleOverlay = (id: Overlay) => {
    const next = new Set(activeOverlays);
    if (next.has(id)) next.delete(id);
    else next.add(id);
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

      {/* Scope (mutually exclusive). Hidden when only one scope is offered —
          the surface is locked (e.g. the Communities lens). */}
      {scopes.length > 1 && (
      <div className={`${clusterVisibility} gap-0.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm p-1 shadow-sm`}>
        {scopes.map((m) => {
          const Icon = m.icon;
          const isActive = activeScope === m.id;
          return (
            <button
              key={m.id}
              onClick={() => setScope(m.id)}
              className={`flex items-center gap-1.5 px-2 py-1.5 rounded-md text-[10px] font-medium transition-all ${
                isActive
                  ? "bg-[var(--color-accent-primary)]/15 text-[var(--color-accent-primary)]"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-overlay)]"
              }`}
              title={m.hint}
              aria-label={m.label}
              aria-pressed={isActive}
            >
              <Icon className="w-3 h-3" />
              <span className="hidden lg:inline">{m.label}</span>
            </button>
          );
        })}
      </div>
      )}

      {/* Overlays (additive signal chips) — not applicable in the constellation */}
      {!isConstellation && (
      <div className={`${clusterVisibility} gap-0.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm p-1 shadow-sm`}>
        {OVERLAYS.map((o) => {
          const Icon = o.icon;
          const isActive = activeOverlays.has(o.id);
          return (
            <button
              key={o.id}
              onClick={() => toggleOverlay(o.id)}
              className={`flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium transition-all ${
                isActive
                  ? "bg-[var(--color-accent-graph)]/15 text-[var(--color-accent-graph)]"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-overlay)]"
              }`}
              title={`Overlay: ${o.label}`}
              aria-label={`Overlay: ${o.label}`}
              aria-pressed={isActive}
            >
              <Icon className="w-3 h-3" />
              <span className="hidden lg:inline">{o.label}</span>
            </button>
          );
        })}
      </div>
      )}

      {/* Layout · color · actions collapse into one floating group so the
          canvas isn't fenced in by a row of separate shadowed pills. */}
      <div className={`${clusterVisibility} items-center gap-1 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm p-1 shadow-sm`}>
        <div className="flex gap-0.5">
          {isConstellation ? (
            // Constellation is locked to the radial layout; show a single
            // active indicator instead of the Force/Hierarchical toggle.
            <button
              key={RADIAL_LAYOUT.id}
              disabled
              className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium bg-[var(--color-accent-graph)]/15 text-[var(--color-accent-graph)] cursor-default"
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
              return (
                <button
                  key={m.id}
                  onClick={() => onLayoutModeChange(m.id)}
                  className={`flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium transition-all ${
                    isActive
                      ? "bg-[var(--color-accent-graph)]/15 text-[var(--color-accent-graph)]"
                      : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-overlay)]"
                  }`}
                  title={m.label}
                  aria-label={m.label}
                  aria-pressed={isActive}
                >
                  <Icon className="w-3 h-3" />
                </button>
              );
            })
          )}
        </div>

        <div className="flex gap-0.5 border-l border-[var(--color-border-default)] pl-1">
          {COLOR_MODES.map((m) => {
            const Icon = m.icon;
            const isActive = colorMode === m.id;
            return (
              <button
                key={m.id}
                onClick={() => onColorModeChange(m.id)}
                className={`flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium transition-all ${
                  isActive
                    ? "bg-[var(--color-accent-graph)]/15 text-[var(--color-accent-graph)]"
                    : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-overlay)]"
                }`}
                title={m.label}
                aria-label={m.label}
                aria-pressed={isActive}
              >
                <Icon className="w-3 h-3" />
              </button>
            );
          })}
        </div>

        <div className="flex gap-0.5 border-l border-[var(--color-border-default)] pl-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onGraphThemeChange(graphTheme === "light" ? "dark" : "light")}
            className={`h-7 w-7 p-0 ${graphTheme === "dark" ? "text-[var(--color-accent-graph)]" : "text-[var(--color-text-tertiary)]"}`}
            title={graphTheme === "dark" ? "Light graph theme" : "Dark graph theme"}
            aria-label={graphTheme === "dark" ? "Light graph theme" : "Dark graph theme"}
            aria-pressed={graphTheme === "dark"}
          >
            {graphTheme === "dark" ? <Sun className="w-3.5 h-3.5" /> : <Moon className="w-3.5 h-3.5" />}
          </Button>
          {/* Path finder / execution flows operate on file-level nodes and
              don't apply to the community constellation — hidden there. */}
          {!isConstellation && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onTogglePathFinder}
            className={`h-7 w-7 p-0 ${showPathFinder ? "text-[var(--color-accent-graph)]" : "text-[var(--color-text-tertiary)]"}`}
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
            className={`h-7 w-7 p-0 ${showFlows ? "text-[var(--color-accent-graph)]" : "text-[var(--color-text-tertiary)]"}`}
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
            className={`h-7 w-7 p-0 ${hideTests ? "text-[var(--color-accent-graph)]" : "text-[var(--color-text-tertiary)]"}`}
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
            className="h-7 w-7 p-0 text-[var(--color-text-tertiary)]"
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
              className="h-7 w-7 p-0 text-[var(--color-text-tertiary)]"
              title="Keyboard shortcuts (?)"
              aria-label="Keyboard shortcuts"
            >
              <HelpCircle className="w-3.5 h-3.5" />
            </Button>
          )}
        </div>
      </div>

      <div className="relative">
        <div className="flex items-center gap-1 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm px-2 py-1 shadow-sm">
          <Search className="w-3 h-3 text-[var(--color-text-tertiary)] shrink-0" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            onKeyDown={onSearchKeyDown}
            placeholder="Search nodes…"
            aria-label="Search graph nodes"
            className="bg-transparent text-xs text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] outline-none w-28 lg:w-40"
          />
          {searchQuery && searchMatchCount != null && searchTotalCount != null && (
            <span className="text-[10px] text-[var(--color-text-tertiary)] tabular-nums whitespace-nowrap">
              {searchMatchCount} / {searchTotalCount}
            </span>
          )}
          {searchQuery && (
            <button
              onClick={() => onSearchChange("")}
              aria-label="Clear search"
              className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
});
