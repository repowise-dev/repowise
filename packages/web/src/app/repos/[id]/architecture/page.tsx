"use client";

/**
 * Architecture — `/repos/[id]/architecture`.
 *
 * The "how the code is wired" destination, with tabs behind `?view=`:
 *   - map      — the constellation, surfaced as the "Communities" tab
 *   - explore  — the full / module dependency graph with dead/hot overlays
 *   - symbols  — the searchable symbol index
 *   - deps     — the third-party dependency registry
 *
 * The curated layered view ("Knowledge Graph") is a separate top-level route
 * (`/knowledge-graph`); the legacy `?view=layers` alias redirects there.
 *
 * Communities and Explore are the SAME `GraphFlow` canvas differing only by
 * scope: Communities locks it to the constellation (radial) scope, Explore
 * mounts it at full/module scope. Switching scope inside Explore keeps `?view=`
 * in sync without remounting.
 */

import { use, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQueryState, parseAsStringLiteral } from "nuqs";
import { Code2 } from "lucide-react";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";
import { GraphView } from "@/components/architecture/graph-view";
import { DependenciesView } from "@/components/architecture/dependencies-view";
import { SymbolTableWrapper as SymbolTable } from "@/components/symbols/symbol-table-wrapper";
import { SymbolIndexHeader } from "@repowise-dev/ui/symbols";
import { COUPLING_DISCLAIMER } from "@repowise-dev/ui/coupling";
import { CouplingTab } from "@/components/coupling/coupling-tab";

// The curated layered view now lives under the dedicated Knowledge Graph route.
const KNOWLEDGE_GRAPH_VIEWS = new Set(["layers"]);

// Accepted `?view=` values. "graph" and "layers" are legacy aliases that are
// normalized / redirected below; only the canonical tabs render a tab.
const VIEWS = [
  "map",
  "explore",
  "deps",
  "symbols",
  "coupling",
  "graph",
  "layers",
] as const;
type ArchView = (typeof VIEWS)[number];

// IA-as-data. Communities first (today's `map`), then the wiring surfaces.
// Coupling is added here in a later phase — the structure is kept easy to
// extend (append a `{ id: "coupling", ... }` row + a panel branch).
const CANONICAL_VIEWS: { id: Extract<ArchView, "map" | "explore" | "deps" | "symbols" | "coupling">; label: string; hint: string }[] = [
  { id: "map", label: "Communities", hint: "Constellation of detected communities" },
  { id: "explore", label: "Explore", hint: "Full dependency graph with dead/hot overlays" },
  { id: "coupling", label: "Coupling", hint: "Files that tend to change together" },
  { id: "deps", label: "Dependencies", hint: "Declared third-party dependencies" },
  { id: "symbols", label: "Symbols", hint: "Every function, class and export" },
];

// Legacy ?view=graph deep links carried the graph scope in ?viewMode=. Scopes
// that render file-level graphs map to Explore; the constellation maps to the
// Communities tab.
const EXPLORE_VIEW_MODES = new Set(["module", "full", "dead", "hotfiles", "unified"]);

export default function ArchitecturePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);
  const router = useRouter();
  const [rawView, setView] = useQueryState(
    "view",
    parseAsStringLiteral(VIEWS).withDefault("map"),
  );
  const [viewModeParam] = useQueryState("viewMode");

  // The curated layers view now lives at /knowledge-graph. `?view=layers`
  // redirects there so shared links keep working.
  const redirectsToKnowledgeGraph = KNOWLEDGE_GRAPH_VIEWS.has(rawView);
  useEffect(() => {
    if (redirectsToKnowledgeGraph) {
      router.replace(`/repos/${repoId}/knowledge-graph`);
    }
  }, [redirectsToKnowledgeGraph, repoId, router]);

  // Normalize the remaining legacy alias without a URL rewrite: ?view=graph →
  // map/explore by scope.
  let view: ArchView = rawView;
  if (rawView === "graph") {
    view = EXPLORE_VIEW_MODES.has(viewModeParam ?? "") ? "explore" : "map";
  }

  const handleScopeViewChange = useCallback(
    (next: "map" | "explore") => {
      void setView(next);
    },
    [setView],
  );

  // The active canonical tab. Map/Explore are the only graph-canvas tabs;
  // everything else resolves to its own panel.
  const activeTab: (typeof CANONICAL_VIEWS)[number]["id"] =
    view === "explore" || view === "deps" || view === "symbols" || view === "coupling"
      ? view
      : "map";

  if (redirectsToKnowledgeGraph) {
    return null;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 px-4 pt-3 sm:px-6">
        <ViewTabs
          tabs={CANONICAL_VIEWS.map((v) => ({ id: v.id, label: v.label }))}
          value={activeTab}
          onValueChange={(id) => void setView(id as ArchView)}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {activeTab === "map" && (
          <GraphView
            repoId={repoId}
            scope="map"
            onScopeViewChange={handleScopeViewChange}
          />
        )}
        {activeTab === "explore" && (
          <GraphView
            repoId={repoId}
            scope="explore"
            onScopeViewChange={handleScopeViewChange}
          />
        )}
        {activeTab === "deps" && <DependenciesView repoId={repoId} />}
        {activeTab === "symbols" && (
          <div className="max-w-[1600px] space-y-6 p-4 sm:p-6">
            <SymbolIndexHeader />
            <SymbolTable repoId={repoId} />
          </div>
        )}
        {activeTab === "coupling" && (
          <div className="mx-auto max-w-[1100px] p-4 sm:p-6">
            <div className="mb-2">
              <h1 className="mb-1 flex items-center gap-2 text-xl font-semibold text-[var(--color-text-primary)]">
                <Code2 className="h-5 w-5 text-[var(--color-accent-primary)]" />
                Change coupling
              </h1>
              <p className="text-sm text-[var(--color-text-secondary)]">
                {COUPLING_DISCLAIMER}
              </p>
            </div>
            <CouplingTab repoId={repoId} />
          </div>
        )}
      </div>
    </div>
  );
}
