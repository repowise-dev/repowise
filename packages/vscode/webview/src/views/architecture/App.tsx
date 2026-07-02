/**
 * Architecture map webview.
 *
 * Reimplements the web Knowledge Graph orchestration (packages/web
 * c4-view.tsx) natively in the editor: the shared c4 canvas, store, layout
 * hook, legend, controls, minimap, and detail panels, driven by one
 * architectureView() RPC instead of a fetch. The web version syncs its
 * navigation to URL query params via nuqs; a webview has no URL, so the same
 * state lives in the shared Zustand store and the only external seam is the
 * optional `selectPath` open-parameter, applied once after the data lands.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ReactFlowProvider,
  useReactFlow,
  type Node,
} from "@xyflow/react";
import {
  ArchBreadcrumb,
  ArchCanvas,
  ArchTourButton,
  CodeViewer,
  FilterPanel,
  NodeTypeCategoryFilters,
  PathFinderModal,
  PersonaSelector,
  SearchBar,
  Sidebar,
  useArchitectureLayout,
  useArchitectureNavigation,
  useArchitectureStore,
  type ArchitectureView,
} from "@repowise-dev/ui/c4";
import type { ViewProps } from "../../runtime/mount";
import type { WebviewHost } from "../../runtime/rpc";

// Double-click drills into structural nodes; the same event on a file-backed
// node opens it in an editor column instead. Single click always selects.
const DRILL_CLUSTER = "layerCluster";
const DRILL_SUBGROUP = "subGroupCluster";
const DRILL_CONTAINER = "archContainer";
const FILE_NODE = "archFile";
// Portals and the scope frame are wayfinding stubs, not selectable entities.
const SYNTHETIC_NODE_TYPES = new Set(["portal", "scopeFrame"]);

export function App(props: ViewProps<"architecture">) {
  return (
    <ReactFlowProvider>
      <ArchitectureMap {...props} />
    </ReactFlowProvider>
  );
}

function ArchitectureMap({ host, repo, params, refreshToken }: ViewProps<"architecture">) {
  const { view, error, loading } = useArchitectureData(host, refreshToken);

  const setView = useArchitectureStore((s) => s.setView);
  const selectNode = useArchitectureStore((s) => s.selectNode);
  const drillIntoLayer = useArchitectureStore((s) => s.drillIntoLayer);
  const navigationLevel = useArchitectureStore((s) => s.navigationLevel);
  const activeLayerId = useArchitectureStore((s) => s.activeLayerId);
  const selectedNodeId = useArchitectureStore((s) => s.selectedNodeId);
  const setReactFlowInstance = useArchitectureStore((s) => s.setReactFlowInstance);
  const pathFinderOpen = useArchitectureStore((s) => s.pathFinderOpen);
  const nodesById = useArchitectureStore((s) => s.nodesById);

  useArchitectureNavigation();
  const { nodes, edges, loading: layoutLoading, hiddenEdgeCount } = useArchitectureLayout();
  const { fitView } = useReactFlow();

  // Feed the store from the RPC payload; setView resets navigation to the
  // overview, which is the right landing tier after a (re)fetch.
  useEffect(() => {
    if (view) setView(view);
  }, [view, setView]);

  // `selectPath` reveals a file's node once the data is in the store. Applied
  // a single time per data load so a later manual selection is never yanked
  // back. Missing paths degrade to the plain overview.
  const preselectAppliedRef = useRef(false);
  useEffect(() => {
    preselectAppliedRef.current = false;
  }, [view]);
  useEffect(() => {
    if (!view || preselectAppliedRef.current) return;
    preselectAppliedRef.current = true;
    const target = params.selectPath;
    if (!target) return;
    const match = view.nodes.find((n) => n.file_path === target);
    if (match) selectNode(match.id);
  }, [view, params.selectPath, selectNode]);

  // Frame the tier after a level change (drill in/out), matching the web
  // camera behaviour so the new scope lands centered.
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
    const raf = requestAnimationFrame(() => fitView({ duration: 400, padding: 0.2 }));
    return () => cancelAnimationFrame(raf);
  }, [nodes, fitView]);

  // Ease onto a freshly selected node once, without re-centering after the
  // user pans away (dimming refreshes must not steal the camera).
  const lastEasedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedNodeId) {
      lastEasedRef.current = null;
      return;
    }
    if (lastEasedRef.current === selectedNodeId) return;
    if (!nodes.some((n) => n.id === selectedNodeId)) return;
    lastEasedRef.current = selectedNodeId;
    const raf = requestAnimationFrame(() =>
      fitView({ nodes: [{ id: selectedNodeId }], duration: 250, padding: 0.4, maxZoom: 1.15 }),
    );
    return () => cancelAnimationFrame(raf);
  }, [selectedNodeId, nodes, fitView]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (SYNTHETIC_NODE_TYPES.has(node.type ?? "")) return;
      selectNode(node.id);
    },
    [selectNode],
  );

  const handleNodeDoubleClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const store = useArchitectureStore.getState();
      if (node.type === DRILL_CLUSTER) {
        drillIntoLayer(node.id);
      } else if (node.type === DRILL_SUBGROUP) {
        store.drillIntoSubGroup(node.id);
      } else if (node.type === DRILL_CONTAINER) {
        store.toggleContainer(node.id);
      } else if (node.type === FILE_NODE) {
        const archNode = nodesById.get(node.id);
        if (archNode?.file_path) {
          host.openFile(archNode.file_path, archNode.line_range?.[0]);
        }
      }
    },
    [drillIntoLayer, nodesById, host],
  );

  const handlePaneClick = useCallback(() => selectNode(null), [selectNode]);

  // The in-panel code viewer reads bytes over the same RPC seam the web build
  // reads from the file-content endpoint.
  const fetchContent = useCallback(
    (filePath: string) => host.api.fileContent(filePath),
    [host],
  );

  const anyLoading = loading || layoutLoading;
  const levelLabel =
    navigationLevel === "layer-detail"
      ? "Detail"
      : navigationLevel === "layer-groups"
        ? "Groups"
        : "Overview";

  return (
    <div
      data-testid="architecture-shell"
      className="flex h-screen flex-col bg-[var(--color-bg-root)] text-[var(--color-text-primary)]"
    >
      <header className="shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-sm font-semibold text-[var(--color-text-primary)]">
                {repo.name}
              </h1>
              <span className="rounded-full border border-[var(--color-border-default)] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">
                {levelLabel}
              </span>
            </div>
            <p className="mt-0.5 truncate text-xs text-[var(--color-text-secondary)]">
              {view?.project_description || "Curated layered view of the system."}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <ArchTourButton />
            <PersonaSelector />
            <NodeTypeCategoryFilters />
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-4">
          <SearchBar />
          <ArchBreadcrumb />
        </div>
      </header>

      <div data-testid="architecture-canvas" className="relative min-h-0 flex-1">
        <ArchCanvas
          nodes={nodes}
          edges={edges}
          loading={anyLoading}
          error={error ? { message: error.message } : null}
          hiddenEdgeCount={hiddenEdgeCount}
          onInit={setReactFlowInstance}
          onNodeClick={handleNodeClick}
          onNodeDoubleClick={handleNodeDoubleClick}
          onPaneClick={handlePaneClick}
          errorTitle="Couldn't load the architecture map"
          loadingLabel="Loading architecture…"
        >
          <Sidebar />
          <FilterPanel />
          <CodeViewer fetchContent={fetchContent} />
          {pathFinderOpen && <PathFinderModal />}
        </ArchCanvas>
      </div>
    </div>
  );
}

interface ArchitectureData {
  view: ArchitectureView | null;
  error: Error | null;
  loading: boolean;
}

/** One architectureView() call, refetched when the index moves. */
function useArchitectureData(host: WebviewHost, refreshToken: number): ArchitectureData {
  const [state, setState] = useState<ArchitectureData>({
    view: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    host.api.architectureView().then(
      (view) => {
        if (!cancelled) setState({ view, error: null, loading: false });
      },
      (err: unknown) => {
        if (!cancelled) {
          setState({
            view: null,
            error: err instanceof Error ? err : new Error(String(err)),
            loading: false,
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [host, refreshToken]);

  return state;
}
