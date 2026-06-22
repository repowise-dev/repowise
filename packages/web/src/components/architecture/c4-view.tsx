"use client";

/**
 * Knowledge Graph view — the curated layered architecture surface.
 *
 * URL params are synced bidirectionally with the Zustand store so that
 * refresh + share preserve the view state.
 */

import { useCallback, useEffect, useRef } from "react";
import { useQueryState, parseAsString, parseAsStringLiteral } from "nuqs";
import {
  ArchCanvas,
  useArchitectureStore,
  useArchitectureLayout,
  useArchitectureNavigation,
  SearchBar,
  ArchBreadcrumb,
  PersonaSelector,
  ArchTourButton,
  NodeTypeCategoryFilters,
  FilterPanel,
  CodeViewer,
  PathFinderModal,
  type Persona,
} from "@repowise-dev/ui/c4";
import { ReactFlowProvider, useReactFlow, type Node } from "@xyflow/react";
import { useArchitectureView } from "@/lib/hooks/use-architecture";
import { ArchDetailPanelHost } from "@/components/c4/arch-detail-panel-host";

const VIEW_VALUES = ["overview", "groups", "detail"] as const;
const PERSONA_VALUES = ["overview", "learn", "deep-dive"] as const;
// Unified click grammar (kg-ux plan B5): single click = select + inspect on
// every node kind. Only true non-entities stay unselectable — portals are
// navigation stubs, the scope frame is a pointer-events-none underlay.
const SYNTHETIC_NODE_TYPES = new Set(["portal", "scopeFrame"]);

/**
 * The curated layered architecture view, ready to mount client-side. The
 * Knowledge Graph route renders this inside a `PageShell` (passing
 * `embedded` so the inner title band is suppressed and the page owns the
 * heading). Must be a client component because it drives a Zustand store and
 * `useReactFlow`.
 */
export function KnowledgeGraphView({
  repoId,
  repoName,
}: {
  repoId: string;
  repoName: string;
}) {
  return (
    <ReactFlowProvider>
      <ArchitectureViewInner repoId={repoId} repoName={repoName} embedded />
    </ReactFlowProvider>
  );
}

function ArchitectureViewInner({
  repoId,
  repoName,
  embedded,
}: {
  repoId: string;
  repoName: string;
  embedded?: boolean;
}) {
  const { view, error, isLoading } = useArchitectureView(repoId);
  const { fitView } = useReactFlow();

  // Renamed from "view" — the parent Architecture page owns `?view=` for its
  // sub-view switcher (graph | c4 | symbols).
  const [, setViewParam] = useQueryState(
    "c4view",
    parseAsStringLiteral(VIEW_VALUES).withDefault("overview"),
  );
  const [layerParam, setLayerParam] = useQueryState("layer", parseAsString);
  const [groupParam, setGroupParam] = useQueryState("group", parseAsString);
  const [nodeParam, setNodeParam] = useQueryState("node", parseAsString);
  const [personaParam, setPersonaParam] = useQueryState(
    "persona",
    parseAsStringLiteral(PERSONA_VALUES).withDefault("overview"),
  );

  const setView = useArchitectureStore((s) => s.setView);
  const navigationLevel = useArchitectureStore((s) => s.navigationLevel);
  const activeLayerId = useArchitectureStore((s) => s.activeLayerId);
  const activeSubGroupId = useArchitectureStore((s) => s.activeSubGroupId);
  const selectedNodeId = useArchitectureStore((s) => s.selectedNodeId);
  const persona = useArchitectureStore((s) => s.persona);
  const drillIntoLayer = useArchitectureStore((s) => s.drillIntoLayer);
  const drillIntoSubGroup = useArchitectureStore((s) => s.drillIntoSubGroup);
  const selectNode = useArchitectureStore((s) => s.selectNode);
  const setPersona = useArchitectureStore((s) => s.setPersona);
  const setReactFlowInstance = useArchitectureStore((s) => s.setReactFlowInstance);
  const pathFinderOpen = useArchitectureStore((s) => s.pathFinderOpen);

  useEffect(() => {
    if (view) setView(view);
  }, [view, setView]);

  const initializedRef = useRef(false);
  useEffect(() => {
    if (!view || initializedRef.current) return;
    initializedRef.current = true;

    if (personaParam && personaParam !== "overview") {
      setPersona(personaParam as Persona);
    }
    if (layerParam) {
      drillIntoLayer(layerParam);
    }
    if (groupParam) {
      drillIntoSubGroup(groupParam);
    }
    if (nodeParam) {
      selectNode(nodeParam);
    }
  }, [view, layerParam, groupParam, nodeParam, personaParam,
      drillIntoLayer, drillIntoSubGroup, selectNode, setPersona]);

  const syncingRef = useRef(false);
  useEffect(() => {
    // Until the deep-link restore above has run, the store still holds its
    // default overview state — syncing that to the URL would null out the
    // very params the restore is about to read, so every shared link
    // snapped back to the overview.
    if (!initializedRef.current) return;
    if (syncingRef.current) return;
    syncingRef.current = true;
    void setViewParam(
      navigationLevel === "layer-detail"
        ? "detail"
        : navigationLevel === "layer-groups"
          ? "groups"
          : "overview",
    );
    void setLayerParam(activeLayerId);
    void setGroupParam(activeSubGroupId);
    void setNodeParam(selectedNodeId);
    void setPersonaParam(persona);
    syncingRef.current = false;
  }, [navigationLevel, activeLayerId, activeSubGroupId, selectedNodeId, persona,
      setViewParam, setLayerParam, setGroupParam, setNodeParam, setPersonaParam]);

  useArchitectureNavigation();

  const { nodes, edges, loading: layoutLoading, hiddenEdgeCount } = useArchitectureLayout();

  const pendingFitRef = useRef(false);
  const prevNavRef = useRef({ navigationLevel, activeLayerId });
  useEffect(() => {
    if (
      prevNavRef.current.navigationLevel !== navigationLevel ||
      prevNavRef.current.activeLayerId !== activeLayerId
    ) {
      pendingFitRef.current = true;
      prevNavRef.current = { navigationLevel, activeLayerId };
    }
  }, [navigationLevel, activeLayerId]);

  useEffect(() => {
    if (!pendingFitRef.current || nodes.length === 0) return;
    pendingFitRef.current = false;
    const raf = requestAnimationFrame(() => {
      fitView({ duration: 400, padding: 0.2 });
    });
    return () => cancelAnimationFrame(raf);
  }, [nodes, fitView]);

  // Camera ease on select (kg-ux plan B5): frame the selected node without
  // drilling. Eases once per selection — layout refreshes (dimming etc.)
  // never re-center a camera the user has panned away.
  const lastEasedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedNodeId) {
      lastEasedRef.current = null;
      return;
    }
    if (lastEasedRef.current === selectedNodeId) return;
    if (!nodes.some((n) => n.id === selectedNodeId)) return;
    lastEasedRef.current = selectedNodeId;
    const raf = requestAnimationFrame(() => {
      fitView({ nodes: [{ id: selectedNodeId }], duration: 250, padding: 0.4, maxZoom: 1.15 });
    });
    return () => cancelAnimationFrame(raf);
  }, [selectedNodeId, nodes, fitView]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (SYNTHETIC_NODE_TYPES.has(node.type ?? "")) return;
      selectNode(node.id);
    },
    [selectNode],
  );

  // Double click = drill (grammar): layer → groups/detail, group → detail,
  // folder → expand/collapse. (Drilling clears selection in the store.)
  const handleNodeDoubleClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (node.type === "layerCluster") {
        drillIntoLayer(node.id);
      } else if (node.type === "subGroupCluster") {
        useArchitectureStore.getState().drillIntoSubGroup(node.id);
      } else if (node.type === "archContainer") {
        useArchitectureStore.getState().toggleContainer(node.id);
      }
    },
    [drillIntoLayer],
  );

  const handlePaneClick = useCallback(() => {
    selectNode(null);
  }, [selectNode]);

  const fetchContent = useCallback(
    async (filePath: string) => {
      const apiBase =
        typeof window !== "undefined"
          ? (process.env.NEXT_PUBLIC_REPOWISE_API_URL ?? "")
          : "";
      const apiKey =
        typeof window !== "undefined"
          ? localStorage.getItem("repowise_api_key")
          : null;
      const res = await fetch(
        `${apiBase}/api/repos/${repoId}/file-content?` +
          new URLSearchParams({ file_path: filePath }).toString(),
        { headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {} },
      );
      if (!res.ok) {
        throw new Error(`Failed to fetch file content (${res.status})`);
      }
      return res.text();
    },
    [repoId],
  );

  const anyLoading = isLoading || layoutLoading;

  return (
    <>
      <div className="shrink-0 px-4 sm:px-6 py-3 border-b border-[var(--color-border-default)]">
        <div className="flex items-center justify-between">
          {!embedded ? (
            <div>
              <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">
                Layers
              </h1>
              <p className="text-xs text-[var(--color-text-secondary)] mt-0.5">
                {repoName} — curated layered view of the system.
              </p>
            </div>
          ) : (
            <span aria-hidden />
          )}
          <div className="flex items-center gap-3">
            <ArchTourButton />
            <PersonaSelector />
            <NodeTypeCategoryFilters />
          </div>
        </div>
        <div className="mt-2 flex items-center gap-4">
          <SearchBar />
          <ArchBreadcrumb />
        </div>
      </div>

      <div className="flex-1 min-h-0 relative">
        {/* The layered canvas chrome (ReactFlow host, controls, minimap, owl
            chips, weaker-link chip, legend) now lives in `ui/c4` so it upgrades
            via a package bump. Web keeps store/data wiring and the panel hosts. */}
        <ArchCanvas
          nodes={nodes}
          edges={edges}
          loading={anyLoading}
          error={error}
          hiddenEdgeCount={hiddenEdgeCount}
          onInit={setReactFlowInstance}
          onNodeClick={handleNodeClick}
          onNodeDoubleClick={handleNodeDoubleClick}
          onPaneClick={handlePaneClick}
          errorTitle="Couldn't load the architecture layers"
          loadingLabel="Loading layers…"
        >
          {/* Orientation and the tour player render in the right Sidebar only. */}
          <ArchDetailPanelHost repoId={repoId} />
          <FilterPanel />
          <CodeViewer fetchContent={fetchContent} />
          {pathFinderOpen && <PathFinderModal />}
        </ArchCanvas>
      </div>
    </>
  );
}
