"use client";

/**
 * Architecture — `/repos/[id]/architecture`.
 *
 * Tabs are DATASETS, not zoom levels:
 *   - Map       — the dependency graph, at community or file scope
 *   - Coupling  — files that tend to change together
 *   - Packages  — the declared third-party dependency registry
 *   - Symbols   — the searchable symbol index
 *
 * How zoomed out the graph is — communities vs files — is a different axis and
 * has exactly one control, `GraphScopeSwitcher`, in the section header beside
 * the graph. It used to be steered from here *and* from a pill cluster floating
 * on the canvas, which put "Communities" on screen twice and left "Explore" as
 * a tab meaning "the graph, but not communities".
 *
 * The tab named "Packages" was "Dependencies", which collided with the graph
 * itself: the Map tab *is* the dependency graph, so a sibling tab called
 * Dependencies read as the same thing at a different zoom. It lists declared
 * third-party packages, so it says that.
 *
 * ## URL state
 *
 * One param per axis, no two params saying the same thing:
 *   - `?view=`   communities | files | coupling | packages | symbols
 *   - `?signal=` dead | hot — which overlay is lit on the graph
 *   - `?module=` a path prefix the file scope is filtered to
 *
 * `?view=` and `?viewMode=` used to encode the same axis twice — `view=explore`
 * and `viewMode=full` both meant "the file graph", and they could disagree.
 * Scope now lives in `?view=` and nothing else carries it. Old links keep
 * working: the legacy spellings are aliased below, and a legacy `?viewMode=` is
 * translated into `?view=`/`?signal=` on first read.
 *
 * The curated layered view ("Knowledge Graph") is a separate top-level route
 * (`/knowledge-graph`); the legacy `?view=layers` alias redirects there.
 */

import { use, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQueryState, parseAsStringLiteral } from "nuqs";
import { Code2 } from "lucide-react";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";
import { ErrorBoundary } from "@repowise-dev/ui/shared";
import { GraphView } from "@/components/architecture/graph-view";
import { DependenciesView } from "@/components/architecture/dependencies-view";
import { SymbolTableWrapper as SymbolTable } from "@/components/symbols/symbol-table-wrapper";
import { SymbolIndexHeader } from "@repowise-dev/ui/symbols";
import { COUPLING_DISCLAIMER } from "@repowise-dev/ui/coupling";
import { CouplingTab } from "@/components/coupling/coupling-tab";

// The curated layered view now lives under the dedicated Knowledge Graph route.
const KNOWLEDGE_GRAPH_VIEWS = new Set(["layers"]);

/** Canonical `?view=` values. The first two are both the Map tab. */
const CANONICAL = ["communities", "files", "coupling", "packages", "symbols"] as const;
type CanonicalView = (typeof CANONICAL)[number];

/** Everything `?view=` accepts, canonical values plus legacy spellings. */
const VIEWS = [...CANONICAL, "map", "explore", "deps", "graph", "layers"] as const;
type ArchView = (typeof VIEWS)[number];

/** Legacy `?view=` spellings → canonical. `graph` predates the tab split and
 *  `explore` was the file-graph half of the old Map/Explore pair. */
const VIEW_ALIASES: Record<string, CanonicalView> = {
  map: "communities",
  graph: "communities",
  explore: "files",
  deps: "packages",
};

/** Legacy `?viewMode=` values → the `?view=` + `?signal=` they now mean. */
const LEGACY_VIEW_MODES: Record<string, { view: CanonicalView; signal?: "dead" | "hot" }> = {
  architecture: { view: "communities" },
  // The modules scope is gone; its nearest honest destination is the file
  // graph, where "modules" is now a filter rather than a separate canvas.
  module: { view: "files" },
  full: { view: "files" },
  dead: { view: "files", signal: "dead" },
  hotfiles: { view: "files", signal: "hot" },
  // "unified" lit dead AND hot at once; the node filter is exclusive now, so
  // it resolves to the one the old toolbar rendered as active.
  unified: { view: "files", signal: "dead" },
};

// Which tab a canonical view renders under. Both graph scopes are the Map tab.
const TAB_FOR_VIEW: Record<CanonicalView, string> = {
  communities: "map",
  files: "map",
  coupling: "coupling",
  packages: "packages",
  symbols: "symbols",
};

const TABS: { id: string; label: string }[] = [
  { id: "map", label: "Map" },
  { id: "coupling", label: "Coupling" },
  { id: "packages", label: "Third-party" },
  { id: "symbols", label: "Symbols" },
];

/** Landing view when a tab is clicked. Map opens on communities — the whole
 *  repo at a size you can read, rather than 1,500 circles. */
const DEFAULT_VIEW_FOR_TAB: Record<string, CanonicalView> = {
  map: "communities",
  coupling: "coupling",
  packages: "packages",
  symbols: "symbols",
};

export default function ArchitecturePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);
  const router = useRouter();
  const [rawView, setView] = useQueryState(
    "view",
    parseAsStringLiteral(VIEWS).withDefault("communities"),
  );
  const [viewModeParam, setViewModeParam] = useQueryState("viewMode");
  const [, setSignal] = useQueryState("signal");
  const [, setModule] = useQueryState("module");
  const [, setFocus] = useQueryState("focus");

  // The curated layers view now lives at /knowledge-graph. `?view=layers`
  // redirects there so shared links keep working.
  const redirectsToKnowledgeGraph = KNOWLEDGE_GRAPH_VIEWS.has(rawView);
  useEffect(() => {
    if (redirectsToKnowledgeGraph) {
      router.replace(`/repos/${repoId}/knowledge-graph`);
    }
  }, [redirectsToKnowledgeGraph, repoId, router]);

  // Translate a legacy `?viewMode=` once, then drop it. Doing this as an effect
  // (rather than reading it every render) means the URL converges on the new
  // shape instead of carrying both spellings forever.
  const legacy = viewModeParam ? LEGACY_VIEW_MODES[viewModeParam] : undefined;
  useEffect(() => {
    if (!legacy) return;
    void setView(legacy.view);
    void setSignal(legacy.signal ?? null);
    void setViewModeParam(null);
  }, [legacy, setView, setSignal, setViewModeParam]);

  const view: CanonicalView =
    legacy?.view ?? VIEW_ALIASES[rawView] ?? (rawView as CanonicalView);
  const activeTab = TAB_FOR_VIEW[view] ?? "map";

  // Leaving a tab drops the params that only meant something inside it, so the
  // URL never carries a `signal=hot` into the Packages table where nothing
  // reads it and nothing shows it.
  const handleTabChange = useCallback(
    (id: string) => {
      void setView(DEFAULT_VIEW_FOR_TAB[id] ?? "communities");
      if (id !== "coupling") void setFocus(null);
      if (id !== "map") {
        void setSignal(null);
        void setModule(null);
      }
    },
    [setView, setFocus, setSignal, setModule],
  );

  const handleScopeChange = useCallback(
    (next: "communities" | "files") => {
      void setView(next);
    },
    [setView],
  );

  if (redirectsToKnowledgeGraph) {
    return null;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 px-4 pt-3 sm:px-6">
        <ViewTabs tabs={TABS} value={activeTab} onValueChange={handleTabChange} />
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {activeTab === "map" && (
          <GraphView
            repoId={repoId}
            scope={view === "files" ? "files" : "communities"}
            onScopeChange={handleScopeChange}
          />
        )}
        {activeTab === "packages" && <DependenciesView repoId={repoId} />}
        {activeTab === "symbols" && (
          <div className="max-w-[1600px] space-y-6 p-4 sm:p-6">
            <SymbolIndexHeader />
            <SymbolTable repoId={repoId} />
          </div>
        )}
        {activeTab === "coupling" && (
          <div className="mx-auto max-w-[1500px] p-4 sm:p-6">
            {/* Wider than the other tabs: the pairs table carries a sentence
                per row plus both modules. The ring keeps its own 820px cap. */}
            <div className="mb-2">
              <h1 className="mb-1 flex items-center gap-2 text-xl font-semibold text-[var(--color-text-primary)]">
                <Code2 className="h-5 w-5 text-[var(--color-accent-primary)]" />
                Change coupling
              </h1>
              <p className="text-sm text-[var(--color-text-secondary)]">
                {COUPLING_DISCLAIMER}
              </p>
            </div>
            {/* Contain a render throw to the tab instead of letting it reach
                the route boundary and blank the page. */}
            <ErrorBoundary title="Couldn't load change coupling">
              <CouplingTab repoId={repoId} />
            </ErrorBoundary>
          </div>
        )}
      </div>
    </div>
  );
}
