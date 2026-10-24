"use client";

/**
 * The Live System Map: the workspace's services and the typed relationships
 * between them, drawn a level at a time within a fixed draw budget. The
 * estate shows group cards joined by bands (or services, below the grouping
 * threshold); a group opens in place into its services; focus and between
 * levels come from the level state. Whatever does not fit is folded into a
 * "+N more" card and stated in the caption.
 *
 * Built on `GraphCanvasShell`: the lens and extra actions in the title row,
 * the family chips and key above the canvas, the caption under it, and the
 * details in the rail, a grid peer of the canvas (a bottom sheet on phones).
 * The host supplies data and routes through props only; it may control the
 * level state and the selection to keep them in its URL.
 */

import { useCallback, useEffect, useMemo, useRef, type ReactNode, type RefObject } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import type { NodeArchitectureRole, SystemGraph } from "@repowise-dev/types";
import { GraphCanvasShell } from "../../graph/graph-canvas-shell";
import { activateEdge, activateNode, type Activation, type ActivateTrigger } from "./activate";
import { toFlowElements, type FlowElements } from "./flow-elements";
import type { LevelView } from "./levels";
import { scopeCaption } from "./scope-caption";
import { SystemMapActionsContext, type SystemMapActions } from "./system-map-actions";
import { SystemMapBreadcrumb } from "./system-map-breadcrumb";
import { SystemMapCanvas } from "./system-map-canvas";
import type { SystemMapDrawerData } from "./system-map-drawer";
import { SystemMapCanvasEmpty } from "./system-map-empty";
import { SystemMapFilters } from "./system-map-filters";
import { SystemMapFocusContext, createSystemMapFocusStore, type SystemMapFocusStore } from "./system-map-focus";
import { SystemMapLegend, legendKeyFor } from "./system-map-legend";
import { railContent } from "./system-map-rail-content";
import type { RepoHealth, SystemMapOverlay, SystemMapSelection } from "./types";
import { useDropUndrawnAggregate, useMapLevel, type MapLevel } from "./use-map-level";
import type { SystemMapPhase } from "./use-system-map-phases";
import { useSystemMapState, type SystemMapState } from "./use-system-map-state";
import { backOut, closeContainer, openContainer, toggleFamily, type MapViewState } from "./view-state";

/** The canvas's height, shared with the host's loading placeholder so nothing reflows. */
export const SYSTEM_MAP_CANVAS_HEIGHT = "h-[420px] sm:h-[clamp(460px,calc(100dvh-240px),800px)]";

const FADE_CSS = ".sm-canvas[data-focused] .sm-edge:not([data-active]){opacity:.22}.sm-canvas .sm-edge{transition:opacity 120ms}";

export interface SystemMapProps {
  graph: SystemGraph | null;
  loading?: boolean;
  error?: Error | null;
  /** Repo health by repo alias, joined onto service and group cards (optional). */
  healthByRepo?: ReadonlyMap<string, RepoHealth>;
  /** Additive decoration from the active lens (ripple, badges, violations). */
  overlay?: SystemMapOverlay;
  /** Per-service architecture role, named on cards and in the rail (optional). */
  roleByNodeId?: ReadonlyMap<string, NodeArchitectureRole>;
  /** The section heading, in the title row beside the lens. */
  title?: string;
  /** The lens control, in the title row. */
  toolbar?: ReactNode;
  /** More title-row controls after the lens (search, a between picker). */
  actions?: ReactNode;
  /** Data and routes for the rail's service and relationship details. */
  drawer?: SystemMapDrawerData;
  /** Controlled selection, so the host can put it in the URL. */
  selection?: SystemMapSelection;
  onSelectionChange?: (selection: SystemMapSelection) => void;
  /** Controlled level state (`open`, `focus`, `a`/`b`, `kinds` in a URL). */
  view?: MapViewState;
  onViewChange?: (view: MapViewState) => void;
  /** Rail content for the current level when nothing is selected, e.g. the routes between two services. */
  levelRail?: ReactNode;
  /** Lists what a "+N more" card folded; the card offers it only when present. */
  onShowFolded?: (serviceIds: readonly string[]) => void;
  /** The breadcrumb's root, e.g. the workspace name. */
  estateLabel?: string;
  /** Called as each render phase completes, for host-side timing. */
  onPhase?: (phase: SystemMapPhase) => void;
}

export function SystemMap(props: SystemMapProps) {
  return (
    <ReactFlowProvider>
      <SystemMapInner {...props} />
    </ReactFlowProvider>
  );
}

/** The connector store, with the canvas's fade attribute and the selection kept in step with it. */
function useFocusStore(level: MapLevel) {
  const focus = useMemo(() => createSystemMapFocusStore(), []);
  const canvasRef = useRef<HTMLDivElement>(null);
  useEffect(() => focus.subscribe(() => canvasRef.current?.toggleAttribute("data-focused", focus.focused())), [focus]);
  const drawnId = level.resolved?.drawnId ?? null;
  const kind = level.resolved?.drawnKind ?? null;
  useEffect(() => {
    focus.setSelected({ node: kind === "node" ? drawnId : null, edge: kind === "edge" ? drawnId : null });
  }, [focus, kind, drawnId]);
  return { focus, canvasRef };
}

/** Card verbs and the canvas's activate and Escape handlers, over the level state and the selection. */
function useMapInput(levelView: LevelView | null, state: SystemMapState, onShowFolded: SystemMapProps["onShowFolded"]) {
  const { view, setView, selection, setSelection } = state;
  const actions = useMemo<SystemMapActions>(
    () => ({
      openContainer: (id) => setView(openContainer(view, id)),
      closeContainer: (id) => setView(closeContainer(view, id)),
      showFolded: onShowFolded,
    }),
    [setView, view, onShowFolded],
  );
  const apply = useCallback(
    (a: Activation) => {
      if (a?.kind === "select") setSelection(a.selection);
      else if (a?.kind === "open") actions.openContainer(a.id);
      else if (a?.kind === "folded") actions.showFolded?.(a.serviceIds);
    },
    [setSelection, actions],
  );
  const onActivate = useCallback(
    (id: string, kind: "node" | "edge", trigger: ActivateTrigger) =>
      apply(
        kind === "edge"
          ? activateEdge(levelView?.edges.find((e) => e.id === id))
          : activateNode(levelView?.nodes.find((n) => n.id === id), trigger),
      ),
    [apply, levelView],
  );
  const onEscape = useCallback(() => {
    if (selection) return setSelection(null);
    const up = backOut(view);
    if (up) setView(up);
  }, [selection, setSelection, view, setView]);
  return { actions, onActivate, onEscape };
}

function SystemMapInner(props: SystemMapProps) {
  const { graph, healthByRepo, overlay, roleByNodeId, toolbar, actions: extraActions } = props;
  const state = useSystemMapState(props);
  const { view, setView, selection, setSelection } = state;
  const level = useMapLevel(graph, view, selection, setView, props.onPhase);
  useDropUndrawnAggregate(selection, level, setSelection);
  const { nodesById, edgesById, levelView, layout } = level;
  const { focus, canvasRef } = useFocusStore(level);
  const input = useMapInput(levelView, state, props.onShowFolded);
  const selectedId = level.resolved?.drawnId ?? null;

  const flow = useMemo<FlowElements>(
    () =>
      levelView
        ? toFlowElements({ view: levelView, positions: layout.positions, nodesById, edgesById, healthByRepo, overlay, roleByNodeId, selectedId })
        : { nodes: [], edges: [] },
    [levelView, layout.positions, nodesById, edgesById, healthByRepo, overlay, roleByNodeId, selectedId],
  );
  const name = useCallback((id: string) => nodesById.get(id)?.name ?? id, [nodesById]);
  const caption = useMemo(() => (levelView ? scopeCaption(levelView, name) : []), [levelView, name]);
  const legend = useMemo(() => legendKeyFor(levelView?.edges ?? [], edgesById), [levelView, edgesById]);

  const hasGraph = Boolean(graph && graph.nodes.length > 0);
  const rail =
    graph && levelView
      ? railContent({
          graph,
          levelView,
          selection,
          nodesById,
          edgesById,
          healthByRepo,
          roleByNodeId,
          drawer: props.drawer,
          levelRail: props.levelRail,
          onSelect: setSelection,
          onOpenGroup: input.actions.openContainer,
        })
      : null;
  const breadcrumb =
    graph && levelView && levelView.level !== "estate" ? (
      <SystemMapBreadcrumb graph={graph} level={levelView.level} view={view} onViewChange={setView} estateLabel={props.estateLabel} />
    ) : undefined;
  const banner = levelView ? (
    <div className="flex min-w-0 flex-col gap-2">
      <SystemMapFilters
        familyCounts={levelView.familyCounts}
        kindCounts={level.kindCounts}
        families={levelView.families}
        onToggleFamily={(f) => setView(toggleFamily(view, f))}
      />
      <SystemMapLegend {...legend} />
    </div>
  ) : undefined;
  const empty = (
    <SystemMapCanvasEmpty
      error={props.error}
      loading={Boolean(props.loading) || (hasGraph && layout.loading && flow.nodes.length === 0)}
      hasGraph={hasGraph}
      hasEdges={(graph?.edges.length ?? 0) > 0}
    />
  );

  return (
    <SystemMapFocusContext.Provider value={focus}>
      <SystemMapActionsContext.Provider value={input.actions}>
        <GraphCanvasShell
          variant="section"
          breadcrumb={breadcrumb}
          title={props.title}
          titleActions={toolbar || extraActions ? <>{toolbar}{extraActions}</> : undefined}
          banner={banner}
          rail={rail}
          footer={hasGraph && caption.length > 0 ? <p className="text-xs text-[var(--color-text-secondary)]">{caption.join(" ")}</p> : undefined}
        >
          <MapCanvas
            canvasRef={canvasRef}
            focus={focus}
            flow={flow}
            fitKey={[levelView?.level, view.open.join(","), view.focus, view.between?.a, view.between?.b].join("|")}
            settled={!layout.loading}
            onActivate={input.onActivate}
            onEscape={input.onEscape}
            onPaneClick={() => setSelection(null)}
            empty={empty}
          />
        </GraphCanvasShell>
      </SystemMapActionsContext.Provider>
    </SystemMapFocusContext.Provider>
  );
}

interface MapCanvasProps {
  canvasRef: RefObject<HTMLDivElement | null>;
  focus: SystemMapFocusStore;
  flow: FlowElements;
  /** Changes with the level; the canvas refits once its layout has landed. */
  fitKey: string;
  settled: boolean;
  onActivate: (id: string, kind: "node" | "edge", trigger: ActivateTrigger) => void;
  onEscape: () => void;
  onPaneClick: () => void;
  empty: ReactNode;
}

function MapCanvas({ canvasRef, focus, flow, fitKey, settled, onActivate, onEscape, onPaneClick, empty }: MapCanvasProps) {
  return (
    <div ref={canvasRef} className={`sm-canvas relative min-w-0 ${SYSTEM_MAP_CANVAS_HEIGHT}`}>
      <style>{FADE_CSS}</style>
      <SystemMapCanvas
        nodes={flow.nodes}
        edges={flow.edges}
        focus={focus}
        fitKey={fitKey}
        settled={settled}
        onActivate={onActivate}
        onPaneClick={onPaneClick}
        onEscape={onEscape}
        empty={empty}
      />
    </div>
  );
}
