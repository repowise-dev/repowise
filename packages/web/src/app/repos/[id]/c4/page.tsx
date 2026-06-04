"use client";

/**
 * C4 / Architecture diagrams — `/repos/[id]/c4`.
 *
 * Supports two modes:
 *   - mode=c4          — legacy C4 three-level diagram
 *   - mode=architecture — new unified architecture view (default)
 *
 * URL params are synced bidirectionally with the Zustand store so that
 * refresh + share preserve the view state.
 */

import { use, useCallback, useEffect, useRef } from "react";
import { useQueryState, parseAsInteger, parseAsString, parseAsStringLiteral } from "nuqs";
import {
  C4Diagram,
  type C4Level,
  useArchitectureStore,
  useArchitectureLayout,
  useArchitectureNavigation,
  archNodeTypes,
  archEdgeTypes,
  SearchBar,
  ArchBreadcrumb,
  ProjectOverview,
  LearnPanel,
  PersonaSelector,
  NodeTypeCategoryFilters,
  FilterPanel,
  CodeViewer,
  PathFinderModal,
  type Persona,
  KEYFRAMES,
} from "@repowise-dev/ui/c4";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useC4L1, useC4L2, useC4L3 } from "@/lib/hooks/use-c4";
import { useArchitectureView } from "@/lib/hooks/use-architecture";
import { useC4DocsPathSet } from "@/lib/hooks/use-c4-context";
import { useRepo } from "@/lib/hooks/use-repo";
import { getC4Mermaid } from "@/lib/api/c4";
import { C4DetailPanelHost } from "@/components/c4/c4-detail-panel-host";
import { ArchDetailPanelHost } from "@/components/c4/arch-detail-panel-host";

const MODE_VALUES = ["c4", "architecture"] as const;
const VIEW_VALUES = ["overview", "groups", "detail"] as const;
const PERSONA_VALUES = ["overview", "learn", "deep-dive"] as const;
// Unified click grammar (kg-ux plan B5): single click = select + inspect on
// every node kind. Only true non-entities stay unselectable — portals are
// navigation stubs, the scope frame is a pointer-events-none underlay.
const SYNTHETIC_NODE_TYPES = new Set(["portal", "scopeFrame"]);

function clampLevel(n: number | null): C4Level {
  return n === 1 ? 1 : n === 3 ? 3 : 2;
}

export default function C4Page({ params }: { params: Promise<{ id: string }> }) {
  const { id: repoId } = use(params);
  const { repo } = useRepo(repoId);

  const [mode] = useQueryState(
    "mode",
    parseAsStringLiteral(MODE_VALUES).withDefault("architecture"),
  );

  return (
    <div className="flex flex-col h-full">
      {mode === "c4" ? (
        <LegacyC4View repoId={repoId} repoName={repo?.name ?? "System"} />
      ) : (
        <ArchitectureViewPage repoId={repoId} repoName={repo?.name ?? "System"} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Architecture View (new)
// ---------------------------------------------------------------------------

function ArchitectureViewPage({ repoId, repoName }: { repoId: string; repoName: string }) {
  return (
    <ReactFlowProvider>
      <ArchitectureViewInner repoId={repoId} repoName={repoName} />
    </ReactFlowProvider>
  );
}

function ArchitectureViewInner({ repoId, repoName }: { repoId: string; repoName: string }) {
  const { view, error, isLoading } = useArchitectureView(repoId);
  const { fitView } = useReactFlow();

  const [, setViewParam] = useQueryState(
    "view",
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
  const tourActive = useArchitectureStore((s) => s.tourActive);

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
    (_: React.MouseEvent, node: { id: string; type?: string }) => {
      if (SYNTHETIC_NODE_TYPES.has(node.type ?? "")) return;
      selectNode(node.id);
    },
    [selectNode],
  );

  // Double click = drill (grammar): layer → groups/detail, group → detail,
  // folder → expand/collapse. (Drilling clears selection in the store.)
  const handleNodeDoubleClick = useCallback(
    (_: React.MouseEvent, node: { id: string; type?: string }) => {
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
          <div>
            <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">
              Knowledge Graph
            </h1>
            <p className="text-xs text-[var(--color-text-secondary)] mt-0.5">
              {repoName} — layers, nodes, and relationships.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <PersonaSelector />
            <NodeTypeCategoryFilters />
          </div>
        </div>
        <div className="mt-2 flex items-center gap-4">
          <SearchBar />
          <ArchBreadcrumb />
        </div>
      </div>

      <style>{KEYFRAMES.accentPulse}{KEYFRAMES.edgeFlow}{`
        /* Zoom-into-tier feel: nodes glide to their next slot (plan D). */
        @media (prefers-reduced-motion: no-preference) {
          .react-flow__node { transition: transform 180ms ease; }
        }
      `}</style>
      <div className="flex-1 min-h-0 relative bg-[var(--color-bg-canvas)]">
        {error && (
          <div className="absolute inset-0 flex items-center justify-center text-[var(--color-error)] text-sm z-10 pointer-events-none">
            {error.message}
          </div>
        )}
        {anyLoading && nodes.length === 0 && !error && (
          <div className="absolute inset-0 flex items-center justify-center text-[var(--color-text-secondary)] text-sm z-10 pointer-events-none">
            Loading knowledge graph…
          </div>
        )}

        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={archNodeTypes}
          edgeTypes={archEdgeTypes}
          onNodeClick={handleNodeClick}
          onNodeDoubleClick={handleNodeDoubleClick}
          onPaneClick={handlePaneClick}
          onInit={setReactFlowInstance}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.1}
          maxZoom={3}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
        >
          {/* Blueprint graph paper: 24px line grid on the warm canvas, matching
              the mermaid container (kg-ux plan §2.1). */}
          <Background variant={BackgroundVariant.Lines} gap={24} size={1} color="var(--color-diagram-grid)" />
          <Controls showInteractive={false} />
          {/* maskColor comes from --xy-minimap-mask-background (theme-aware). */}
          <MiniMap pannable zoomable />
        </ReactFlow>

        {/* Orientation-first landing: story before scene (plan C-1). */}
        {navigationLevel === "overview" && !selectedNodeId && !tourActive && (
          <aside
            aria-label="Project orientation"
            className="absolute top-3 left-3 bottom-3 z-10 w-72 overflow-y-auto rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated,rgba(17,24,39,0.96))] shadow-xl"
          >
            <ProjectOverview />
          </aside>
        )}

        {/* Guided tour player — the canvas follows each step (plan C-2). */}
        {tourActive && (
          <aside
            aria-label="Guided tour"
            className="absolute top-3 left-3 z-10 w-72 max-h-[calc(100%-24px)] overflow-y-auto rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated,rgba(17,24,39,0.96))] shadow-xl"
          >
            <LearnPanel />
          </aside>
        )}

        {hiddenEdgeCount > 0 && (
          <div
            className="absolute bottom-4 left-14 z-10 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-secondary)]/90 px-3 py-1 text-xs text-[var(--color-text-secondary)]"
            title="Weakest aggregated connections are hidden to keep the view legible. Drill in to see them."
          >
            +{hiddenEdgeCount} weaker link{hiddenEdgeCount === 1 ? "" : "s"} hidden
          </div>
        )}

        <ArchDetailPanelHost repoId={repoId} />

        <FilterPanel />

        <CodeViewer fetchContent={fetchContent} />

        {pathFinderOpen && <PathFinderModal />}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Legacy C4 View (preserved for backward compat)
// ---------------------------------------------------------------------------

function LegacyC4View({ repoId, repoName }: { repoId: string; repoName: string }) {
  const [levelParam, setLevelParam] = useQueryState(
    "level",
    parseAsInteger.withDefault(2),
  );
  const [containerParam, setContainerParam] = useQueryState(
    "container",
    parseAsString,
  );

  const level = clampLevel(levelParam);
  const activeContainerId = containerParam || null;

  const { view: l1View, error: l1Err, isLoading: l1Loading } = useC4L1(level === 1 ? repoId : null);
  const { view: l2View, error: l2Err, isLoading: l2Loading } = useC4L2(level >= 2 ? repoId : null);
  const { view: l3View, error: l3Err, isLoading: l3Loading } = useC4L3(
    level === 3 ? repoId : null,
    level === 3 ? activeContainerId : null,
  );

  const setLevel = useCallback(
    (next: C4Level) => {
      void setLevelParam(next);
      if (next !== 3) void setContainerParam(null);
    },
    [setContainerParam, setLevelParam],
  );

  const drillInto = useCallback(
    (containerId: string) => {
      void setLevelParam(3);
      void setContainerParam(containerId);
    },
    [setContainerParam, setLevelParam],
  );

  const drillOut = useCallback(() => {
    if (level === 3) {
      void setLevelParam(2);
      void setContainerParam(null);
    } else if (level === 2) {
      void setLevelParam(1);
    }
  }, [level, setContainerParam, setLevelParam]);

  const loading = level === 1 ? l1Loading : level === 2 ? l2Loading : l3Loading;
  const error = level === 1 ? l1Err : level === 2 ? l2Err : l3Err;

  const { pathSet: docsPathSet, pageIdByPath } = useC4DocsPathSet(repoId);

  const fetchMermaid = useCallback(
    () => getC4Mermaid(repoId, level, activeContainerId),
    [activeContainerId, level, repoId],
  );

  return (
    <>
      <div className="shrink-0 px-4 sm:px-6 py-3 border-b border-[var(--color-border-default)]">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">
              Knowledge Graph
            </h1>
            <p className="text-xs text-[var(--color-text-secondary)] mt-0.5">
              System context, containers, and components — drill in to navigate.
            </p>
          </div>
          {/* Legacy mode is frozen (locked decision 4) — point at the new view. */}
          <a
            href="?mode=architecture"
            className="shrink-0 rounded-full border border-[var(--color-accent-primary,#f59520)]/50 bg-[var(--color-accent-primary,#f59520)]/10 px-3 py-1 text-xs text-[var(--color-accent-primary,#f59520)] hover:bg-[var(--color-accent-primary,#f59520)]/20"
          >
            Try the new architecture view →
          </a>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        <C4Diagram
          level={level}
          activeContainerId={activeContainerId}
          systemName={repoName}
          l1View={l1View}
          l2View={l2View}
          l3View={l3View}
          loading={loading}
          error={error}
          onLevelChange={setLevel}
          onDrillInto={drillInto}
          onDrillOut={drillOut}
          docsPathSet={docsPathSet}
          fetchMermaid={fetchMermaid}
          renderInspector={({ data, onClose, onDrillIn }) => (
            <C4DetailPanelHost
              repoId={repoId}
              data={data}
              pageIdByPath={pageIdByPath}
              onClose={onClose}
              onDrillIn={onDrillIn}
            />
          )}
        />
      </div>
    </>
  );
}
