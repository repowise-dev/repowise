"use client";

/**
 * Architecture — `/repos/[id]/architecture`.
 *
 * One destination for "how is this built", with five modes behind `?view=`:
 *   - map     — the constellation Knowledge Graph (community super-graph)
 *   - layers  — the curated layered architecture view (tour, personas)
 *   - explore — the full / module dependency graph with dead/hot overlays
 *   - symbols — the searchable symbol index
 *   - deps    — the third-party dependency registry
 * The legacy C4 diagram stays reachable behind `?view=c4` until the layered
 * view reaches full parity.
 *
 * Map and Explore share one canvas component (GraphFlow) and therefore one
 * search, one path finder, one legend and one inspector; switching scope
 * inside the canvas keeps `?view=` in sync without remounting.
 */

import { use, useCallback } from "react";
import { useQueryState, parseAsStringLiteral } from "nuqs";
import { Code2 } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { GraphView } from "@/components/architecture/graph-view";
import { C4View } from "@/components/architecture/c4-view";
import { DependenciesView } from "@/components/architecture/dependencies-view";
import { SymbolTableWrapper as SymbolTable } from "@/components/symbols/symbol-table-wrapper";

// "graph" and "c4" are accepted as legacy aliases from pre-merge URLs and
// normalized below; only the five canonical views render switcher buttons.
const VIEWS = ["map", "layers", "explore", "symbols", "deps", "graph", "c4"] as const;
type ArchView = (typeof VIEWS)[number];

const CANONICAL_VIEWS: ArchView[] = ["map", "layers", "explore", "symbols", "deps"];

const VIEW_LABELS: Record<ArchView, string> = {
  map: "Map",
  layers: "Layers",
  explore: "Explore",
  symbols: "Symbols",
  deps: "Dependencies",
  graph: "Map",
  c4: "C4 (legacy)",
};

const VIEW_HINTS: Record<string, string> = {
  map: "Constellation of detected communities",
  layers: "Curated layered view with a guided tour",
  explore: "Full dependency graph with dead/hot overlays",
  symbols: "Every function, class and export",
  deps: "Declared third-party dependencies",
};

// Legacy ?view=graph deep links carried the graph scope in ?viewMode=. Scopes
// that render file-level graphs map to Explore; the constellation maps to Map.
const EXPLORE_VIEW_MODES = new Set(["module", "full", "dead", "hotfiles", "unified"]);

export default function ArchitecturePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);
  const [rawView, setView] = useQueryState(
    "view",
    parseAsStringLiteral(VIEWS).withDefault("map"),
  );
  const [viewModeParam] = useQueryState("viewMode");
  const [modeParam] = useQueryState("mode");

  // Normalize legacy aliases without a URL rewrite (cheap, shareable links
  // keep working): graph → map/explore by scope; c4 + mode=architecture was
  // the curated view (now Layers); c4 + mode=c4 stays the frozen legacy mode.
  let view: ArchView = rawView;
  if (rawView === "graph") {
    view = EXPLORE_VIEW_MODES.has(viewModeParam ?? "") ? "explore" : "map";
  } else if (rawView === "c4" && modeParam !== "c4") {
    view = "layers";
  }

  const handleScopeViewChange = useCallback(
    (next: "map" | "explore") => {
      void setView(next);
    },
    [setView],
  );

  const isGraphCanvas = view === "map" || view === "explore";

  return (
    <div className="flex h-full flex-col">
      {/* View switcher */}
      <div className="flex shrink-0 items-center gap-1 border-b border-[var(--color-border-default)] px-4 py-2 sm:px-6 overflow-x-auto">
        {CANONICAL_VIEWS.map((v) => (
          <button
            key={v}
            onClick={() => void setView(v)}
            title={VIEW_HINTS[v]}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs font-medium whitespace-nowrap transition-colors",
              view === v
                ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
            )}
          >
            {VIEW_LABELS[v]}
          </button>
        ))}
        {view === "c4" && (
          <span className="rounded-md bg-[var(--color-accent-muted)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent-primary)] whitespace-nowrap">
            C4 (legacy)
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {isGraphCanvas && (
          <GraphView
            repoId={repoId}
            scope={view as "map" | "explore"}
            onScopeViewChange={handleScopeViewChange}
          />
        )}
        {(view === "layers" || view === "c4") && (
          <C4View repoId={repoId} legacy={view === "c4"} />
        )}
        {view === "deps" && <DependenciesView repoId={repoId} />}
        {view === "symbols" && (
          <div className="max-w-[1600px] space-y-6 p-4 sm:p-6">
            <div>
              <h1 className="mb-1 flex items-center gap-2 text-xl font-semibold text-[var(--color-text-primary)]">
                <Code2 className="h-5 w-5 text-[var(--color-accent-primary)]" />
                Symbol Index
              </h1>
              <p className="text-sm text-[var(--color-text-secondary)]">
                Searchable index of all functions, classes, and exports.
              </p>
            </div>
            <SymbolTable repoId={repoId} />
          </div>
        )}
      </div>
    </div>
  );
}
