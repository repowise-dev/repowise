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
 * The host supplies data, routes and the links loader through props only; it
 * may control the level state and the selection to keep them in its URL.
 * Search, the Map | List switch, keyboard shortcuts, comparing two services
 * and a live line announcing each level change come from
 * `use-map-navigation.ts`; card and rail verbs from `use-map-verbs.ts`.
 */

import { useCallback, useEffect, useMemo, useRef, type ReactNode, type RefObject } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import type { NodeArchitectureRole, SystemGraph } from "@repowise-dev/types";
import { GraphCanvasShell } from "../../graph/graph-canvas-shell";
import { GraphShortcutHelp } from "../../graph/graph-shortcut-help";
import type { ActivateTrigger } from "./activate";
import { levelAnnouncement } from "./announce";
import { toFlowElements, type FlowElements } from "./flow-elements";
import { navCopyWith, type SystemMapNavCopy } from "./nav-copy";
import { scopeCaption } from "./scope-caption";
import type { SystemMapRouteLoader } from "./search-index";
import { shortcutHelpRows } from "./shortcuts";
import { SystemMapActionsContext } from "./system-map-actions";
import { SystemMapCanvas } from "./system-map-canvas";
import { mapChrome } from "./system-map-chrome";
import type { SystemMapDrawerData } from "./system-map-drawer";
import { SystemMapCanvasEmpty } from "./system-map-empty";
import { SystemMapFocusContext, createSystemMapFocusStore, type SystemMapFocusStore } from "./system-map-focus";
import { legendKeyFor } from "./system-map-legend";
import { SystemMapList } from "./system-map-list";
import { SystemMapNavActions } from "./system-map-nav-actions";
import type { LoadLinksBetween } from "./routes-between";
import { railContent } from "./system-map-rail-content";
import type { RepoHealth, SystemMapOverlay, SystemMapSelection } from "./types";
import { useDropUndrawnAggregate, useMapLevel, type MapLevel } from "./use-map-level";
import { useMapNavigation, type MapNavigation } from "./use-map-navigation";
import { useMapInput, useRailVerbs } from "./use-map-verbs";
import type { SystemMapPhase } from "./use-system-map-phases";
import { useSystemMapSearch } from "./use-system-map-search";
import { useSystemMapState, type SystemMapState } from "./use-system-map-state";
import { TRAY_OPEN_TOKEN, type MapViewState } from "./view-state";

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
  /**
   * One page of routes between two services (`GET /api/workspace/system-graph/links`).
   * The Between level lists them in the rail and a relationship shows its own;
   * without it both say route detail is not available.
   */
  loadLinksBetween?: LoadLinksBetween;
  /** Lists what a "+N more" card folded; the card offers it only when present. */
  onShowFolded?: (serviceIds: readonly string[]) => void;
  /** The breadcrumb's root, e.g. the workspace name. */
  estateLabel?: string;
  /** Called as each render phase completes, for host-side timing. */
  onPhase?: (phase: SystemMapPhase) => void;
  /** Contract links matching a search, so search finds routes too; without it search covers services and groups. */
  loadRoutes?: SystemMapRouteLoader;
  /** The navigation's words (search, list, shortcuts, announcements); English by default. */
  navCopy?: Partial<SystemMapNavCopy>;
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

/** What the live region says: the level, the display, or a pending Between pick. */
function useAnnouncement(graph: SystemGraph | null, level: MapLevel, view: MapViewState, nav: MapNavigation, copy: SystemMapNavCopy) {
  const { levelView, nodesById } = level;
  return useMemo(
    () =>
      levelAnnouncement({
        levelView,
        view,
        display: nav.display,
        services: graph?.nodes.length ?? 0,
        pendingFrom: nav.pendingFrom,
        name: (id) => nodesById.get(id)?.name ?? id,
        groupLabel: (id) => (id === TRAY_OPEN_TOKEN ? "Not linked at runtime" : (graph?.groups?.find((g) => g.id === id)?.label ?? id)),
        copy,
      }),
    [graph, levelView, nodesById, view, nav.display, nav.pendingFrom, copy],
  );
}

function SystemMapInner(props: SystemMapProps) {
  const { graph, healthByRepo, overlay, roleByNodeId, toolbar, actions: extraActions } = props;
  const own = useSystemMapState(props);
  const nav = useMapNavigation(own);
  // Every input selects through the navigation, so a pending Between pick completes wherever the second service is picked.
  const state: SystemMapState = { ...own, setSelection: nav.setSelection };
  const { view, setView, selection, setSelection } = state;
  const copy = useMemo(() => navCopyWith(props.navCopy), [props.navCopy]);
  const level = useMapLevel(graph, view, selection, setView, props.onPhase);
  useDropUndrawnAggregate(selection, level, own.setSelection);
  const { nodesById, edgesById, levelView, layout } = level;
  const { focus, canvasRef } = useFocusStore(level);
  const input = useMapInput(levelView, state, props.onShowFolded);
  const verbs = useRailVerbs(state, nav);
  const search = useSystemMapSearch(graph, nav.onSearchPick, props.loadRoutes);
  const announcement = useAnnouncement(graph, level, view, nav, copy);
  const listing = nav.display === "list";
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
  const showCaption = !listing && hasGraph && caption.length > 0;
  const railNode =
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
          loadLinksBetween: props.loadLinksBetween,
          routeFilter: nav.routeFilter,
          onSelect: setSelection,
          onOpenGroup: input.actions.openContainer,
          verbs,
        })
      : null;
  const { breadcrumb, banner, betweenControl } = mapChrome({ graph, level, view, setView, nav, legend, estateLabel: props.estateLabel });
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
          titleActions={
            <>
              {toolbar}
              {extraActions}
              {betweenControl}
              <SystemMapNavActions nav={nav} search={search} copy={copy} name={name} />
            </>
          }
          banner={banner}
          rail={railNode}
          footer={showCaption ? <p className="text-xs text-[var(--color-text-secondary)]">{caption.join(" ")}</p> : undefined}
        >
          <div className="relative min-w-0">
            {listing && graph ? (
              <SystemMapList
                graph={graph}
                healthByRepo={healthByRepo}
                selectedId={selection?.type === "node" ? selection.id : null}
                onSelect={(id) => setSelection({ type: "node", id })}
                copy={copy}
              />
            ) : (
              <MapCanvas
                canvasRef={canvasRef}
                focus={focus}
                flow={flow}
                fitKey={[levelView?.level, view.open.join(","), view.focus, view.between?.a, view.between?.b].join("|")}
                settled={!layout.loading}
                onActivate={input.onActivate}
                onEscape={nav.back}
                onPaneClick={() => setSelection(null)}
                empty={empty}
              />
            )}
            {nav.help && (
              <GraphShortcutHelp
                onClose={() => nav.setHelp(false)}
                shortcuts={shortcutHelpRows(copy)}
                gestures={[]}
                label={copy.helpTitle}
                title={copy.helpOpen}
              />
            )}
            <p role="status" aria-live="polite" className="sr-only">
              {announcement}
            </p>
          </div>
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
  onEscape: () => unknown;
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
