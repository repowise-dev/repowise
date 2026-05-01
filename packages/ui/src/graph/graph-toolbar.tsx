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
} from "lucide-react";
import { Button } from "../ui/button";

export type ColorMode = "language" | "community" | "risk";
export type ViewMode = "module" | "full" | "architecture" | "dead" | "hotfiles";

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
}

const VIEW_MODES: { id: ViewMode; icon: typeof Boxes; label: string }[] = [
  { id: "module", icon: Boxes, label: "Modules" },
  { id: "full", icon: LayoutGrid, label: "Full Graph" },
  { id: "architecture", icon: GitFork, label: "Architecture" },
  { id: "dead", icon: Skull, label: "Dead Code" },
  { id: "hotfiles", icon: Flame, label: "Hot Files" },
];

const COLOR_MODES: { id: ColorMode; icon: typeof Palette; label: string }[] = [
  { id: "language", icon: Palette, label: "Language" },
  { id: "community", icon: Network, label: "Community" },
  { id: "risk", icon: Shield, label: "Risk" },
];

export function GraphToolbar({
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
}: GraphToolbarProps) {
  return (
    <div className="flex flex-col gap-1.5 items-end">
      <div className="flex gap-0.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm p-1 shadow-lg shadow-black/20">
        {VIEW_MODES.map((m) => {
          const Icon = m.icon;
          const isActive = viewMode === m.id;
          return (
            <button
              key={m.id}
              onClick={() => onViewChange(m.id)}
              className={`flex items-center gap-1.5 px-2 py-1.5 rounded-md text-[10px] font-medium transition-all ${
                isActive
                  ? "bg-[var(--color-accent-primary)]/15 text-[var(--color-accent-primary)]"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-overlay)]"
              }`}
              title={m.label}
              aria-label={m.label}
              aria-pressed={isActive}
            >
              <Icon className="w-3 h-3" />
              <span className="hidden lg:inline">{m.label}</span>
            </button>
          );
        })}
      </div>

      <div className="flex gap-1.5 items-center">
        <div className="flex gap-0.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm p-1 shadow-lg shadow-black/20">
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

        <div className="flex gap-0.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm p-1 shadow-lg shadow-black/20">
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
        </div>
      </div>

      <div className="relative">
        <div className="flex items-center gap-1 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/90 backdrop-blur-sm px-2 py-1 shadow-lg shadow-black/20">
          <Search className="w-3 h-3 text-[var(--color-text-tertiary)] shrink-0" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search nodes…"
            aria-label="Search graph nodes"
            className="bg-transparent text-[11px] text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] outline-none w-28 lg:w-40"
          />
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
}
